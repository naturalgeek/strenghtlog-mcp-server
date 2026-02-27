"""StrengthLog MCP Server."""

import os
import logging
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from strengthlog_mcp.strengthlog.client import StrengthLogClient
from strengthlog_mcp.strengthlog.exceptions import AuthenticationError

logger = logging.getLogger(__name__)

mcp = FastMCP("strengthlog")
client = StrengthLogClient()


async def _ensure_login() -> None:
    """Auto-login using env vars if not already authenticated."""
    if client.is_authenticated:
        return

    email = os.environ.get("STRENGTHLOG_EMAIL")
    password = os.environ.get("STRENGTHLOG_PASSWORD")
    if not email or not password:
        raise AuthenticationError(
            "STRENGTHLOG_EMAIL and STRENGTHLOG_PASSWORD environment variables must be set."
        )
    await client.login(email, password)


@mcp.tool()
async def get_workouts(since_days: int | None = None, limit: int = 50) -> str:
    """Fetch workout history from StrengthLog.

    Args:
        since_days: Only return workouts from the last N days. Omit for all workouts.
        limit: Maximum number of workouts to return (default 50).
    """
    await _ensure_login()

    since = None
    if since_days is not None:
        since = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        from datetime import timedelta
        since = since - timedelta(days=since_days)

    workouts = await client.get_workouts(since=since, limit=limit)

    if not workouts:
        return "No workouts found."

    lines = []
    for w in workouts:
        duration = f" ({w.duration_minutes}min)" if w.duration_minutes else ""
        date_str = w.start_time.strftime("%Y-%m-%d %H:%M")
        lines.append(f"## {w.name} — {date_str}{duration}")
        lines.append(f"Total volume: {w.total_volume:.0f} kg | Working sets: {len(w.working_sets)}")

        # Group sets by exercise
        exercises: dict[str, list] = {}
        for s in w.sets:
            exercises.setdefault(s.exercise_name, []).append(s)

        for ex_name, sets in exercises.items():
            working = [s for s in sets if not s.is_warmup]
            warmup = [s for s in sets if s.is_warmup]
            parts = []
            if warmup:
                parts.append(f"  {ex_name} (warmup): " + ", ".join(
                    f"{s.weight_kg}kg x {s.reps}" for s in warmup
                ))
            if working:
                parts.append(f"  {ex_name}: " + ", ".join(
                    f"{s.weight_kg}kg x {s.reps}" + (f" @RPE{s.rpe}" if s.rpe else "")
                    for s in working
                ))
            lines.extend(parts)

        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def get_exercises() -> str:
    """Fetch the user's exercise library from StrengthLog, including custom exercises."""
    await _ensure_login()

    exercises = await client.get_exercises()

    if not exercises:
        return "No exercises found."

    lines = [f"Found {len(exercises)} exercises:\n"]
    for ex in sorted(exercises, key=lambda e: e.name):
        translations = ", ".join(f"{k}: {v}" for k, v in ex.name_translations.items() if k != "en")
        if translations:
            lines.append(f"- **{ex.name}** (id: {ex.id}) [{translations}]")
        else:
            lines.append(f"- **{ex.name}** (id: {ex.id})")

    return "\n".join(lines)


@mcp.tool()
async def get_programs() -> str:
    """List all training programs (user-created, followed, and global)."""
    await _ensure_login()

    programs = await client.get_programs()

    if not programs:
        return "No programs found."

    lines = [f"Found {len(programs)} programs:\n"]
    for p in programs:
        desc = f" — {p.description}" if p.description else ""
        lines.append(f"- **{p.name}** (id: {p.id}, source: {p.source}, {len(p.workouts_order)} workouts){desc}")

    return "\n".join(lines)


@mcp.tool()
async def get_program(program_id: str, source: str = "user_programs") -> str:
    """Fetch full details of a training program including all workouts and exercises.

    Args:
        program_id: The program ID (from get_programs).
        source: Program source — "user_programs", "following", or "global".
    """
    await _ensure_login()

    program = await client.get_program(program_id, source)

    lines = [f"# {program.name}"]
    if program.description:
        lines.append(f"{program.description}\n")

    if not program.workouts:
        lines.append("No workouts found in this program.")
        return "\n".join(lines)

    for w in program.workouts:
        week_str = f" (Week {w.week})" if w.week else ""
        lines.append(f"\n## {w.name}{week_str}")

        # Group sets by exercise, preserving order of first appearance
        exercises: dict[str, list] = {}
        exercise_order: list[str] = []
        for s in w.sets:
            name = s.exercise_name or s.exercise_id
            if name not in exercises:
                exercise_order.append(name)
                exercises[name] = []
            exercises[name].append(s)

        for ex_name in exercise_order:
            sets = exercises[ex_name]
            warmup = [s for s in sets if s.is_warmup]
            working = [s for s in sets if not s.is_warmup]

            lines.append(f"  **{ex_name}**")
            if warmup:
                for s in warmup:
                    w_str = f"{s.weight}kg x " if s.weight else ""
                    lines.append(f"    - Warmup: {w_str}{s.reps} reps")
            for s in working:
                w_str = f"{s.weight}kg x " if s.weight else ""
                lines.append(f"    - {w_str}{s.reps} reps")

    return "\n".join(lines)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
