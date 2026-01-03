def print_event_gray(text: str) -> None:
    """
    Print event/debug output in gray using ANSI escape codes.
    """
    GRAY = "\033[90m"
    RESET = "\033[0m"
    print(f"{GRAY}{text}{RESET}")
