# Some part borrowed from official tutorial https://github.com/pytorch/examples/blob/master/imagenet/main.py
from __future__ import absolute_import
from __future__ import print_function

import copy
import importlib
import logging
import os
import time
from pathlib import Path

from args import parse_args
from utils.logging import parse_configs_file
from utils.hw import hw_loss, hw_flops_loss
from utils.model import map_shortcut_rate

args = parse_args()

parse_configs_file(args)

os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu

import numpy as np
import torch
import torch.nn as nn
from torch.utils.tensorboard import SummaryWriter

import data
import harp
from utils.logging import (
    parse_prune_stg,
    save_checkpoint,
    create_subdirs,
    clone_results_to_latest_subdir,
)
from utils.model import (
    get_layers,
    prepare_model,
    initialize_scaled_score,
    initialize_stg_rate,
    display_loadrate,
    show_gradients,
    current_model_pruned_fraction,
    sanity_check_paramter_updates,
)
from utils.schedules import get_lr_policy, get_optimizer


def main():

    # sanity checks
    if args.exp_mode in ["score_prune", "score_finetune", "rate_prune", "harp_prune", "harp_finetune"] and not args.resume:
        assert args.source_net, "Provide checkpoint to prune/finetune"

    # create resutls dir (for logs, checkpoints, etc.)
    if args.exp_mode == 'pretrain':
        result_main_dir = os.path.join(args.result_dir, 'pretrain')
    else:
        result_main_dir = os.path.join(Path(args.result_dir), args.exp_name, args.exp_mode)

    if os.path.exists(result_main_dir):
        n = len(next(os.walk(result_main_dir))[-2])  # prev experiments with same name
        result_sub_dir = os.path.join(
            result_main_dir,
            "{}--k-{:.2f}_trainer-{}_lr-{}_epochs-{}_warmuplr-{}_warmupepochs-{}".format(
                n + 1,
                args.k,
                args.trainer,
                args.lr,
                args.epochs,
                args.warmup_lr,
                args.warmup_epochs,
            ),
        )
    else:
        os.makedirs(result_main_dir, exist_ok=True)
        result_sub_dir = os.path.join(
            result_main_dir,
            "1--k-{:.2f}_trainer-{}_lr-{}_epochs-{}_warmuplr-{}_warmupepochs-{}".format(
                args.k,
                args.trainer,
                args.lr,
                args.epochs,
                args.warmup_lr,
                args.warmup_epochs,
            ),
        )
    create_subdirs(result_sub_dir)

    if args.exp_mode in ["rate_prune", "harp_prune"]:
        parse_prune_stg(args)

    # add logger
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger()
    logger.addHandler(
        logging.FileHandler(os.path.join(result_sub_dir, "setup.log"), "a")
    )
    logger.info(args)

    # seed cuda
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    np.random.seed(args.seed)

    # Select GPUs
    use_cuda = not args.no_cuda and torch.cuda.is_available()
    num_gpus = len(args.gpu.strip().split(","))
    # gpu_list = [int(i) for i in args.gpu.strip().split(",")]
    gpu_list = [i for i in range(num_gpus)]
    device = torch.device(f"cuda:{gpu_list[0]}" if use_cuda else "cpu")

    # Dataloader
    D = data.__dict__[args.dataset](args)
    train_loader, test_loader = D.data_loaders()

    # Create model
    cl, ll = get_layers(args.layer_type)
    if len(gpu_list) > 1:
        print("Using multiple GPUs")
        model = nn.DataParallel(
            harp.__dict__[args.arch](
                cl, ll, args.init_type, mean=D.mean, std=D.std, num_classes=args.num_classes,
                prune_reg=args.prune_reg, task_mode=args.exp_mode, normalize=args.normalize
            ),
            device_ids=gpu_list,
            output_device=device
        ).to(device)
    else:
        model = harp.__dict__[args.arch](
            cl, ll, args.init_type, mean=D.mean, std=D.std, num_classes=args.num_classes,
            prune_reg=args.prune_reg, task_mode=args.exp_mode, normalize=args.normalize
        ).to(device)
    logger.info(model)

    # Customize models for training/pruning/fine-tuning
    prepare_model(model, args, device)

    # Setup tensorboard writer
    writer = SummaryWriter(os.path.join(result_sub_dir, "tensorboard"))

    logger.info(
        f"Dataset: {args.dataset}, D: {D}, num_train: {len(train_loader.dataset)}, num_test:{len(test_loader.dataset)}")

    # autograd
    criterion = nn.CrossEntropyLoss()
    optimizer = get_optimizer(model, args)
    lr_policy = get_lr_policy(args.lr_schedule)(optimizer, args)
    logger.info([criterion, optimizer, lr_policy])

    # train & val method
    trainer = importlib.import_module(f"trainer.{args.trainer}").train
    val = getattr(importlib.import_module("utils.eval"), args.val_method)

    if args.exp_mode in ['pretrain']:
        best_prec1 = 0

    else:

        assert args.source_net != '' or args.resume != '', (
            "Incorrect setup: "
            "resume => required to resume a previous experiment (loads all parameters)|| "
            "source_net => required to start pruning/fine-tuning from a source model (only load state_dict)"
        )

        # Load source_net (if checkpoint provided). Only load the state_dict (required for pruning and fine-tuning)
        if args.source_net and args.resume == '':
            if os.path.isfile(args.source_net):
                logger.info("=> loading source model from '{}'".format(args.source_net))
                checkpoint = torch.load(args.source_net, map_location=device)
                model_dict = model.state_dict()
                checkpoint_dict = checkpoint['state_dict']
                if args.exp_mode in ['score_prune', 'rate_prune', 'harp_prune']:
                    if args.dataset == 'imagenet' and args.gpu.find(',') == -1:
                        checkpoint_dict = {k.replace("module.", ""): v for k, v in checkpoint_dict.items()
                                           if k.find('popup_scores') == -1 and k.find('sub_block') == -1}
                    elif args.dataset != 'imagenet' and args.gpu.find(',') != -1:
                        checkpoint_dict = {f"module.{k}": v for k, v in checkpoint_dict.items()
                                           if k.find('popup_scores') == -1 and k.find('sub_block') == -1}
                    else:
                        checkpoint_dict = {k.replace("module.basic_model.", ""): v for k, v in checkpoint_dict.items()
                                           if k.find('popup_scores') == -1 and k.find('sub_block') == -1}
                    model_dict.update(checkpoint_dict)
                    model.load_state_dict(model_dict)
                else:
                    model.load_state_dict(checkpoint_dict)
                logger.info("=> loaded checkpoint '{}'".format(args.source_net))
            else:
                logger.info("=> no checkpoint found at '{}'".format(args.source_net))

            best_prec1 = 0

        # resume (if checkpoint provided). Continue training with previous settings.
        else:
            if os.path.isfile(args.resume):
                logger.info("=> loading checkpoint '{}'".format(args.resume))
                checkpoint = torch.load(args.resume, map_location=device)
                args.start_epoch = checkpoint["epoch"]
                best_prec1 = checkpoint["best_prec1"]
                model.load_state_dict(checkpoint["state_dict"])
                optimizer.load_state_dict(checkpoint["optimizer"])
                logger.info(
                    f"=> loaded checkpoint '{args.resume}' (epoch {checkpoint['epoch']}, best_acc1 {best_prec1:.2f}%)"
                )
            else:
                logger.info("=> no checkpoint found at '{}'".format(args.resume))

    # Init scores once source net is loaded.
    # NOTE: scaled_init_scores will overwrite the scores in the pre-trained net.
    if args.scaled_score_init:
        initialize_scaled_score(model, args.prune_reg)

    if args.rate_stg_init:
        initialize_stg_rate(model, args, device, logger)
    elif args.exp_mode != 'pretrain':
        display_loadrate(model, logger, args)

    if args.prune_reg == 'channel':
        map_shortcut_rate(model, args, verbose=True)

    show_gradients(model, logger)

    if args.prune_reg == 'channel':
        _, _, start_rate = hw_flops_loss(model, device, optimizer, args, print_target=True)
    else:
        _, _, start_rate = hw_loss(model, device, optimizer, args, print_target=True)

    logger.info(f'\nStarting from: Prune-rate = {start_rate}\n')

    # Evaluate
    if args.evaluate or args.exp_mode != 'pretrain':
        if args.dataset == 'imagenet' and not args.evaluate:
            print('>> Skip initial evaluation!')
        else:
            p1_bn, _, p1, _, loss, adv_loss = val(model, device, test_loader, criterion, args, writer)
            logger.info(
                f"Benign validation accuracy {args.val_method} for source-net: {p1_bn}, Adversarial validation accuracy {args.val_method} for source-net: {p1}")
        if args.evaluate:
            return

    # Load current model state_dict for sanity check
    last_ckpt = copy.deepcopy(model.state_dict())

    # Capture Loss, Adv Loss, Benign Acc & Adv Acc
    losses = []
    adv_losses = []
    acc_ben = []
    acc_adv = []

    # Start training

    frozen_gamma = None
    reach_hw = False

    start_time = time.time()
    for epoch in range(args.start_epoch, args.epochs + args.warmup_epochs):
        lr_policy(epoch)  # adjust learning rate

        # train
        trainer(
            model,
            device,
            train_loader,
            criterion,
            optimizer,
            epoch,
            args,
            writer,
            frozen_gamma=frozen_gamma
        )
        # evaluate on test set
        prec1_benign, _, prec1, _, loss, adv_loss = val(model, device, test_loader, criterion, args, writer, epoch)
        losses.append(loss)
        adv_losses.append(adv_loss)
        acc_ben.append(prec1_benign.item())
        acc_adv.append(prec1.item())

        # Check current compression rate
        hw_info = ''
        if args.exp_mode != 'pretrain':
            if args.prune_reg == 'channel':
                loss_hw_func = hw_flops_loss
            else:
                loss_hw_func = hw_loss
            gamma, loss_hw, current_rate = loss_hw_func(model, device, optimizer, args, epoch=epoch, frozen_gamma=frozen_gamma)

            if np.round(loss_hw.cpu().data, 4) == 0.0:
                frozen_gamma = gamma
                reach_hw = True

            hw_info = f"gamma {gamma:.4f}, hw-loss {loss_hw:.4f}, compress-rate {current_rate:.4f}, "

        # remember best prec@1 and save checkpoint
        if args.exp_mode in ['rate_prune', 'harp_prune']:
            if reach_hw:
                is_best = prec1 > best_prec1 if args.adv_loss != 'nat' else prec1_benign > best_prec1
                best_prec1 = max(prec1, best_prec1) if args.adv_loss != 'nat' else max(prec1_benign, best_prec1)
            else:
                is_best = False
        else:
            is_best = prec1 > best_prec1 if args.adv_loss != 'nat' else prec1_benign > best_prec1
            best_prec1 = max(prec1, best_prec1) if args.adv_loss != 'nat' else max(prec1_benign, best_prec1)

        save_checkpoint(
            {
                "epoch": epoch + 1,
                "arch": args.arch,
                "state_dict": model.state_dict(),
                "best_prec1": best_prec1,
                "optimizer": optimizer.state_dict(),
            },
            is_best,
            args,
            result_dir=os.path.join(result_sub_dir, "checkpoint"),
            save_dense=args.save_dense,
        )

        best_acc_name = 'best_adv'  # if args.adv_loss != 'nat' else 'best_benign'
        if args.dataset == 'imagenet':
            acc_info = f"adversarial valid-acc {prec1:.4f}, {best_acc_name} {best_prec1:.4f}"
        else:
            acc_info = f"benign valid-acc {prec1_benign:.4f}, adversarial valid-acc {prec1:.4f}, {best_acc_name} {best_prec1:.4f}"

        epoch_info = f"Epoch {epoch}, val-method {args.val_method}, " + hw_info + acc_info
        if is_best:
            epoch_info += f" [update BEST]"

        logger.info(epoch_info)

        if args.exp_mode in ['rate_prune', 'harp_prune']:
            display_loadrate(model, logger, args)

        if args.exp_mode in ["score_prune", "score_finetune"]:
            logger.info(
                "Pruned model: {:.2f}%".format(
                    current_model_pruned_fraction(
                        model, os.path.join(result_sub_dir, "checkpoint"), verbose=False
                    )
                )
            )
        # clone results to latest subdir (sync after every epoch)
        # Latest_subdir: stores results from latest run of an experiment.
        clone_results_to_latest_subdir(
            result_sub_dir, os.path.join(result_main_dir, "latest_exp")
        )

        # Check what parameters got updated in the current epoch.
        sw, ss, sr = sanity_check_paramter_updates(model, last_ckpt)
        logger.info(
            f"Sanity check (exp-mode: {args.exp_mode}): Weight update - {sw}, Scores update - {ss}, Rates update - {sr}"
        )

        print(f"Time since start of training: {float(time.time() - start_time) / 60} minutes")

    end_time = time.time()
    print(
        f"Total training time: {end_time - start_time} seconds. These are {float((end_time - start_time) / 3600)} hours")


if __name__ == "__main__":
    main()
