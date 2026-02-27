"""StrengthLog data models."""

from datetime import datetime
from pydantic import BaseModel


class Exercise(BaseModel):
    """An exercise in the StrengthLog library."""
    id: str
    name: str
    name_translations: dict[str, str] = {}


class ExerciseSet(BaseModel):
    """A single set within a workout."""
    exercise_id: str
    exercise_name: str
    order: int
    reps: int
    weight_kg: float
    is_warmup: bool = False
    rpe: float | None = None

    @property
    def volume(self) -> float:
        return self.weight_kg * self.reps


class Workout(BaseModel):
    """A workout session."""
    id: str
    name: str
    start_time: datetime
    end_time: datetime | None = None
    sets: list[ExerciseSet] = []

    @property
    def duration_minutes(self) -> int | None:
        if self.end_time:
            return int((self.end_time - self.start_time).total_seconds() / 60)
        return None

    @property
    def total_volume(self) -> float:
        return sum(s.volume for s in self.sets if not s.is_warmup)

    @property
    def unique_exercises(self) -> list[str]:
        return list(set(s.exercise_name for s in self.sets))

    @property
    def working_sets(self) -> list[ExerciseSet]:
        return [s for s in self.sets if not s.is_warmup]


class ProgramSet(BaseModel):
    """A set definition within a program workout."""
    exercise_id: str
    exercise_name: str | None = None
    order: int = 0
    reps: int = 0
    weight: float | None = None
    is_warmup: bool = False


class ProgramWorkout(BaseModel):
    """A workout within a program."""
    id: str
    name: str
    week: int | None = None
    sets: list[ProgramSet] = []


class Program(BaseModel):
    """A training program from StrengthLog."""
    id: str
    name: str
    description: str | None = None
    workouts_order: list[str] = []
    source: str = "user_programs"
    workouts: list[ProgramWorkout] | None = None
