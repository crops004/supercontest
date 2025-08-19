# types.py
from typing import TypedDict

class AggRow(TypedDict):
    user_id: int
    username: str
    points: float
    wins: int
    pushes: int
    losses: int
    rank: int
