"""Short syllabus examples kept separate from the working finance application."""
from abc import ABC, abstractmethod
from typing import Final


def recursive_factorial(number: int) -> int:
    """Recursion: each call reduces the input until the base case reaches 1."""
    if number < 0: raise ValueError("Factorial is undefined for negative integers.")
    return 1 if number < 2 else number * recursive_factorial(number - 1)


def describe_person(name: str, age: int = 18) -> str:
    """Python default arguments can cover simple cases that use overloads in Java."""
    return f"{name} is {age} years old."


class ExampleAccount:
    """Python uses conventions: _name is internal; __name triggers name mangling."""
    def __init__(self, balance: float):
        self._balance = balance
        self.__audit_tag = "local-demo"

    def get_balance(self) -> float: return self._balance


LEDGER_NAME: Final[str] = "PaisaLedger"
# Final is a static type-checking hint. Python does not enforce it at runtime.


class Payment(ABC):
    """An abstract base class cannot be instantiated until pay is implemented."""
    @abstractmethod
    def pay(self, amount: float) -> str: ...


class CashPayment(Payment):
    def pay(self, amount: float) -> str: return f"Paid ₹{amount:.2f} in cash."


def console_io_example(input_text: str = "Asha") -> str:
    """A testable console I/O example. Run this module to see input()/print()."""
    return f"Hello, {input_text}!"


if __name__ == "__main__":
    name = input("Enter your name: ")
    print(console_io_example(name))
    print("Factorial of 5:", recursive_factorial(5))
    print(describe_person(name))

