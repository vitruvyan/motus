from enum import Enum


class Policy(str, Enum):
    """
    Execution policy for the Axis graph.

    Policies constrain behavior.
    They do not modify graph structure.
    """

    STRICT = "strict"
    EXPLORATION = "exploration"
