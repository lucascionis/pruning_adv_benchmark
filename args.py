import argparse


# Inherited from https://github.com/yaodongyu/TRADES/blob/master/train_trades_cifar10.py
def parse_args():
    parser = argparse.ArgumentParser(description="PyTorch Training")

    # primary
    parser.add_argument(
        "--configs", type=str, default="", help="configs file",
    )
    parser.add_argument(
        "--result-dir",
        default="./trained_models",
        type=str,
        help="directory to save results",
    )
    parser.add_argument(
        "--exp-name",
        type=str,
        help="Name of the experiment (creates dir with this name in --result-dir)",
    )

    parser.add_argument(
        "--exp-mode",
        type=str,
        choices=("pretrain", "prune", "finetune"),
        help="Train networks following one of these methods.",
    )

    # Pruning
    parser.add_argument(
        "--snip-init",
        action="store_true",
        default=False,
        help="Whether implemnet snip init",
    )

    parser.add_argument(
        "--k",
        type=float,
        default=1.0,
        help="Fraction of weight variables kept in subnet",
    )

    parser.add_argument(
        "--scaled-score-init",
        action="store_true",
        default=False,
        help="Init importance scores proportaional to weights (default kaiming init)",
    )

    parser.add_argument(
        "--scale_rand_init",
        action="store_true",
        default=False,
        help="Init weight with scaling using pruning ratio",
    )

    parser.add_argument(
        "--freeze-bn",
        action="store_true",
        default=False,
        help="freeze batch-norm parameters in pruning",
    )

    parser.add_argument(
        "--source-net",
        type=str,
        default="",
        help="Checkpoint which will be pruned/fine-tuned",
    )

    # Data
    parser.add_argument(
        "--dataset",
        type=str,
        choices=("CIFAR10", "CIFAR100", "SVHN", "MNIST", "imagenet"),
        help="Dataset for training and eval",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=128,
        metavar="N",
        help="input batch size for training (default: 128)",
    )
    parser.add_argument(
        "--test-batch-size",
        type=int,
        default=128,
        metavar="N",
        help="input batch size for testing (default: 128)",
    )
    parser.add_argument(
        "--normalize",
        action="store_true",
        default=False,
        help="whether to normalize the data",
    )
    parser.add_argument(
        "--data-dir", type=str, default="./datasets", help="path to datasets"
    )

    parser.add_argument(
        "--data-fraction",
        type=float,
        default=1.0,
        help="Fraction of images used from training set",
    )
    parser.add_argument(
        "--image-dim", type=int, default=32, help="Image size: dim x dim x 3"
    )
    parser.add_argument(
        "--mean", type=tuple, default=(0, 0, 0), help="Mean for data normalization"
    )
    parser.add_argument(
        "--std", type=tuple, default=(1, 1, 1), help="Std for data normalization"
    )

    # Training
    parser.add_argument(
        "--trainer",
        type=str,
        default="base",
        choices=("base", "adv", "mixtrain", "crown-ibp", "smooth", "freeadv"),
        help="Natural (base) or adversarial or verifiable training",
    )
    parser.add_argument(
        "--epochs", type=int, default=100, metavar="N", help="number of epochs to train"
    )
    parser.add_argument(
        "--optimizer", type=str, default="sgd", choices=("sgd", "adam", "rmsprop")
    )
    parser.add_argument("--wd", default=1e-4, type=float, help="Weight decay")
    parser.add_argument("--lr", type=float, default=0.1, help="learning rate")
    parser.add_argument(
        "--lr-schedule",
        type=str,
        default="cosine",
        choices=("step", "cosine"),
        help="Learning rate schedule",
    )
    parser.add_argument("--momentum", type=float, default=0.9, help="SGD momentum")
    parser.add_argument(
        "--warmup-epochs", type=int, default=0, help="Number of warmup epochs"
    )
    parser.add_argument(
        "--warmup-lr", type=float, default=0.1, help="warmup learning rate"
    )
    parser.add_argument(
        "--save-dense",
        action="store_true",
        default=False,
        help="Save dense model alongwith subnets.",
    )

    # Free-adv training (only for imagenet)
    parser.add_argument(
        "--n-repeats",
        type=int,
        default=4,
        help="--number of repeats in free-adv training",
    )

    # Adversarial attacks
    parser.add_argument("--epsilon", default=8.0 / 255, type=float, help="perturbation")
    parser.add_argument(
        "--num-steps", default=10, type=int, help="perturb number of steps"
    )
    parser.add_argument(
        "--step-size", default=2.0 / 255, type=float, help="perturb step size"
    )
    parser.add_argument("--clip-min", default=0, type=float, help="perturb step size")
    parser.add_argument("--clip-max", default=1.0, type=float, help="perturb step size")
    parser.add_argument(
        "--distance",
        type=str,
        default="l_inf",
        choices=("l_inf", "l_2"),
        help="attack distance metric",
    )
    parser.add_argument(
        "--const-init",
        action="store_true",
        default=False,
        help="use random initialization of epsilon for attacks",
    )
    parser.add_argument(
        "--beta",
        default=6.0,
        type=float,
        help="regularization, i.e., 1/lambda in TRADES",
    )

    # Evaluate
    parser.add_argument(
        "--evaluate", action="store_true", default=False, help="Evaluate model"
    )

    parser.add_argument(
        "--val_method",
        type=str,
        default="base",
        choices=("base", "adv", "mixtrain", "ibp", "smooth", "freeadv"),
        help="base: evaluation on unmodified inputs | adv: evaluate on adversarial inputs",
    )

    return parser.parse_args()
