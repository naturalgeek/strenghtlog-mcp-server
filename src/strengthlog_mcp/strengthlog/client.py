"""StrengthLog API client."""

import logging
from datetime import datetime, timezone

import httpx

from strengthlog_mcp.strengthlog.auth import FirebaseAuth
from strengthlog_mcp.strengthlog.models import (
    Workout, ExerciseSet, Exercise, Program, ProgramWorkout, ProgramSet,
)
from strengthlog_mcp.strengthlog.exceptions import APIError, AuthenticationError

logger = logging.getLogger(__name__)

FIRESTORE_BASE = "https://firestore.googleapis.com/v1/projects/styrkelabbet/databases/(default)/documents"


class StrengthLogClient:
    """Client for interacting with the StrengthLog API via Firebase/Firestore."""

    def __init__(self):
        self._auth = FirebaseAuth()
        self._exercises_cache: dict[str, str] = {}

    @property
    def is_authenticated(self) -> bool:
        return self._auth.is_authenticated

    @property
    def user_id(self) -> str | None:
        return self._auth.user_id

    async def login(self, email: str, password: str) -> None:
        await self._auth.login(email, password)

    async def _ensure_authenticated(self) -> None:
        if not self._auth.is_authenticated:
            raise AuthenticationError("Not authenticated. Call login() first.")
        if self._auth.is_token_expired:
            await self._auth.refresh()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        await self._ensure_authenticated()

        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                f"{FIRESTORE_BASE}{path}",
                headers=self._auth.get_auth_header(),
                **kwargs,
            )

            if response.status_code == 401:
                await self._auth.refresh()
                response = await client.request(
                    method,
                    f"{FIRESTORE_BASE}{path}",
                    headers=self._auth.get_auth_header(),
                    **kwargs,
                )

            if response.status_code >= 400:
                raise APIError(
                    f"API request failed: {response.text}",
                    status_code=response.status_code,
                )

            return response.json()

    async def get_exercises(self) -> list[Exercise]:
        """Fetch the user's exercise library."""
        data = await self._request(
            "GET",
            f"/25users/{self._auth.user_id}/exercises",
            params={"pageSize": 1000},
        )

        exercises = []
        for doc in data.get("documents", []):
            exercise = self._parse_exercise(doc)
            if exercise:
                exercises.append(exercise)
                self._exercises_cache[exercise.id] = exercise.name

        return exercises

    async def get_workouts(
        self,
        since: datetime | None = None,
        limit: int = 500,
    ) -> list[Workout]:
        """Fetch user's workouts with pagination."""
        if not self._exercises_cache:
            await self.get_exercises()

        workouts = []
        all_exercise_ids: set[str] = set()
        next_page_token = None
        page_size = 100

        while True:
            params = {"pageSize": page_size}
            if next_page_token:
                params["pageToken"] = next_page_token

            data = await self._request(
                "GET",
                f"/25users/{self._auth.user_id}/log",
                params=params,
            )

            docs = data.get("documents", [])

            for doc in docs:
                fields = doc.get("fields", {})
                sets_field = fields.get("sets", {}).get("mapValue", {}).get("fields", {})
                for set_data in sets_field.values():
                    sf = set_data.get("mapValue", {}).get("fields", {})
                    ex_id = sf.get("exercise", {}).get("stringValue", "")
                    if ex_id:
                        all_exercise_ids.add(ex_id)

            await self._resolve_exercise_names(all_exercise_ids)

            for doc in docs:
                workout = self._parse_workout(doc)
                if workout:
                    if since:
                        wt = workout.start_time
                        st = since
                        if wt.tzinfo is None:
                            wt = wt.replace(tzinfo=timezone.utc)
                        if st.tzinfo is None:
                            st = st.replace(tzinfo=timezone.utc)
                        if wt < st:
                            continue
                    workouts.append(workout)

            next_page_token = data.get("nextPageToken")
            if not next_page_token:
                break

        return sorted(workouts, key=lambda w: w.start_time, reverse=True)[:limit]

    async def _resolve_exercise_names(self, exercise_ids: set[str]) -> None:
        missing = exercise_ids - set(self._exercises_cache.keys())
        for ex_id in missing:
            try:
                data = await self._request("GET", f"/exercises/{ex_id}")
                fields = data.get("fields", {})
                loc = fields.get("loc", {}).get("mapValue", {}).get("fields", {})
                name = loc.get("en", {}).get("stringValue", "") or loc.get("sv", {}).get("stringValue", "")
                if not name:
                    name_field = fields.get("name", {}).get("mapValue", {}).get("fields", {})
                    name = name_field.get("en", {}).get("stringValue", "") or name_field.get("sv", {}).get("stringValue", "")
                if not name:
                    name = fields.get("name", {}).get("stringValue", "")
                if name:
                    self._exercises_cache[ex_id] = name
            except Exception:
                pass

    def _parse_exercise(self, doc: dict) -> Exercise | None:
        try:
            fields = doc.get("fields", {})
            doc_id = doc["name"].split("/")[-1]

            loc_field = fields.get("loc", {}).get("mapValue", {}).get("fields", {})
            name = loc_field.get("en", {}).get("stringValue", "")
            if not name:
                name = loc_field.get("sv", {}).get("stringValue", "")
            if not name:
                name_field = fields.get("name", {}).get("mapValue", {}).get("fields", {})
                name = name_field.get("en", {}).get("stringValue", "")
                if not name:
                    name = name_field.get("sv", {}).get("stringValue", "")
            if not name:
                name = fields.get("name", {}).get("stringValue", "")
            if not name:
                name = doc_id

            translations = {}
            for lang, val in loc_field.items():
                if "stringValue" in val:
                    translations[lang] = val["stringValue"]

            return Exercise(id=doc_id, name=name, name_translations=translations)
        except Exception:
            return None

    def _parse_workout(self, doc: dict) -> Workout | None:
        try:
            fields = doc.get("fields", {})
            doc_id = doc["name"].split("/")[-1]

            name = "Workout"
            name_field = fields.get("name", {})
            if "mapValue" in name_field:
                name_map = name_field.get("mapValue", {}).get("fields", {})
                for lang in ["en", "sv", "de", "fr", "es"]:
                    lang_name = name_map.get(lang, {}).get("stringValue", "")
                    if lang_name:
                        name = lang_name
                        break
            elif "stringValue" in name_field:
                name = name_field.get("stringValue", "Workout")

            start_val = fields.get("start", {}).get("integerValue", "0")
            end_val = fields.get("end", {}).get("integerValue", "0")
            start_ms = int(start_val) if start_val else 0
            end_ms = int(end_val) if end_val else 0

            start_time = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc) if start_ms > 0 else None
            end_time = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc) if end_ms > 0 else None

            if start_time is None:
                try:
                    timestamp = float(doc_id)
                    if timestamp > 1000000000000:
                        start_time = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
                except (ValueError, TypeError):
                    pass

            if start_time is None:
                start_time = datetime.now(timezone.utc)

            sets_field = fields.get("sets", {}).get("mapValue", {}).get("fields", {})
            sets = []
            for set_id, set_data in sets_field.items():
                exercise_set = self._parse_set(set_data)
                if exercise_set:
                    sets.append(exercise_set)

            sets.sort(key=lambda s: s.order)

            return Workout(
                id=doc_id, name=name, start_time=start_time, end_time=end_time, sets=sets,
            )
        except Exception:
            return None

    def _parse_set(self, set_data: dict) -> ExerciseSet | None:
        try:
            fields = set_data.get("mapValue", {}).get("fields", {})
            exercise_id = fields.get("exercise", {}).get("stringValue", "")
            exercise_name = self._exercises_cache.get(exercise_id, exercise_id)
            order = int(fields.get("order", {}).get("integerValue", 0))
            is_warmup = fields.get("warmup", {}).get("booleanValue", False)

            variables = fields.get("variables", {}).get("mapValue", {}).get("fields", {})
            reps = int(variables.get("reps", {}).get("integerValue", 0))

            weight_micro = int(variables.get("weight", {}).get("integerValue", 0))
            if weight_micro == 0:
                bw_micro = int(variables.get("bodyweight", {}).get("integerValue", 0))
                extra_micro = int(variables.get("extraWeight", {}).get("integerValue", 0))
                weight_micro = bw_micro + extra_micro
            weight_kg = weight_micro / 1_000_000

            rpe_raw = int(variables.get("rpe", {}).get("integerValue", 0))
            rpe = rpe_raw / 1000.0 if rpe_raw > 0 else None

            return ExerciseSet(
                exercise_id=exercise_id,
                exercise_name=exercise_name,
                order=order,
                reps=reps,
                weight_kg=weight_kg,
                is_warmup=is_warmup,
                rpe=rpe,
            )
        except Exception:
            return None

    # --- Firestore helpers ---

    @staticmethod
    def _parse_firestore_value(val: dict):
        if "stringValue" in val:
            return val["stringValue"]
        if "integerValue" in val:
            return int(val["integerValue"])
        if "doubleValue" in val:
            return val["doubleValue"]
        if "booleanValue" in val:
            return val["booleanValue"]
        if "timestampValue" in val:
            return val["timestampValue"]
        if "arrayValue" in val:
            items = val["arrayValue"].get("values", [])
            return [StrengthLogClient._parse_firestore_value(v) for v in items]
        if "mapValue" in val:
            fields = val["mapValue"].get("fields", {})
            return {k: StrengthLogClient._parse_firestore_value(v) for k, v in fields.items()}
        if "nullValue" in val:
            return None
        if "referenceValue" in val:
            return val["referenceValue"]
        return None

    @staticmethod
    def _parse_firestore_doc(doc: dict) -> dict:
        fields = doc.get("fields", {})
        return {k: StrengthLogClient._parse_firestore_value(v) for k, v in fields.items()}

    @staticmethod
    def _extract_localized_name(data: dict, fallback: str = "Unnamed") -> str:
        loc = data.get("loc")
        if isinstance(loc, dict):
            for lang in ["en", "sv", "de", "fr", "es"]:
                if lang in loc and isinstance(loc[lang], str) and loc[lang]:
                    return loc[lang]
            for v in loc.values():
                if isinstance(v, str) and v:
                    return v
        for field in ["name", "title"]:
            val = data.get(field)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, dict):
                for lang in ["en", "sv", "de", "fr", "es"]:
                    if lang in val and isinstance(val[lang], str) and val[lang]:
                        return val[lang]
                for v in val.values():
                    if isinstance(v, str) and v:
                        return v
        return fallback

    async def _fetch_document(self, path: str, fields: list[str] | None = None) -> dict:
        await self._ensure_authenticated()
        params = {}
        if fields:
            params["mask.fieldPaths"] = fields
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{FIRESTORE_BASE}/{path}",
                headers=self._auth.get_auth_header(),
                params=params,
            )
            if response.status_code == 401:
                await self._auth.refresh()
                response = await client.get(
                    f"{FIRESTORE_BASE}/{path}",
                    headers=self._auth.get_auth_header(),
                    params=params,
                )
            if response.status_code >= 400:
                raise APIError(f"Document fetch failed: {response.text}", status_code=response.status_code)
            return response.json()

    async def _fetch_collection(self, path: str, fields: list[str] | None = None) -> list[dict]:
        await self._ensure_authenticated()
        all_docs = []
        next_page_token = None

        while True:
            params: dict = {"pageSize": "100"}
            if next_page_token:
                params["pageToken"] = next_page_token
            if fields:
                params["mask.fieldPaths"] = fields

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{FIRESTORE_BASE}/{path}",
                    headers=self._auth.get_auth_header(),
                    params=params,
                )
                if response.status_code == 401:
                    await self._auth.refresh()
                    response = await client.get(
                        f"{FIRESTORE_BASE}/{path}",
                        headers=self._auth.get_auth_header(),
                        params=params,
                    )
                if response.status_code >= 400:
                    break

                data = response.json()
                docs = data.get("documents", [])
                all_docs.extend(docs)

                next_page_token = data.get("nextPageToken")
                if not next_page_token:
                    break

        return all_docs

    # --- Programs ---

    async def get_programs(self) -> list[Program]:
        """Fetch the user's StrengthLog programs (own + followed)."""
        user_id = self._auth.user_id

        followed_ids: set[str] = set()
        try:
            profile_doc = await self._fetch_document(f"25users/{user_id}")
            profile = self._parse_firestore_doc(profile_doc)
            following = profile.get("followingPrograms")
            if isinstance(following, dict):
                for pid, val in following.items():
                    if isinstance(val, dict) and val.get("following") is False:
                        continue
                    followed_ids.add(pid)
        except Exception:
            pass

        user_docs = await self._fetch_collection(
            f"25users/{user_id}/programs",
            fields=["name", "loc", "workoutsOrder", "description"],
        )

        programs: list[Program] = []
        found_ids: set[str] = set()

        for doc in user_docs:
            doc_id = doc["name"].split("/")[-1]
            data = self._parse_firestore_doc(doc)
            source = "following" if doc_id in followed_ids else "user_programs"
            programs.append(Program(
                id=doc_id,
                name=self._extract_localized_name(data, "Unnamed Program"),
                description=data.get("description") if isinstance(data.get("description"), str) else None,
                workouts_order=self._extract_workouts_order(data),
                source=source,
            ))
            found_ids.add(doc_id)

        for pid in followed_ids:
            if pid in found_ids:
                continue
            try:
                doc = await self._fetch_document(
                    f"programs/{pid}",
                    fields=["name", "loc", "workoutsOrder", "description"],
                )
                data = self._parse_firestore_doc(doc)
                programs.append(Program(
                    id=pid,
                    name=self._extract_localized_name(data, "Unnamed Program"),
                    description=data.get("description") if isinstance(data.get("description"), str) else None,
                    workouts_order=self._extract_workouts_order(data),
                    source="global",
                ))
            except Exception:
                pass

        programs.sort(key=lambda p: (0 if p.source in ("following", "global") else 1, p.name))
        return programs

    async def get_program(self, program_id: str, source: str) -> Program:
        """Fetch a full program with workouts and exercise names."""
        user_id = self._auth.user_id

        if not self._exercises_cache:
            await self.get_exercises()

        if source in ("user_programs", "following"):
            base_path = f"25users/{user_id}/programs/{program_id}"
        else:
            base_path = f"programs/{program_id}"

        program_doc = await self._fetch_document(base_path)
        program_data = self._parse_firestore_doc(program_doc)

        workouts_order = self._extract_workouts_order(program_data)
        all_exercise_ids: set[str] = set()
        workouts: list[ProgramWorkout] = []

        for wid in workouts_order:
            try:
                wdoc = await self._fetch_document(
                    f"{base_path}/workouts/{wid}",
                    fields=["name", "loc", "sets", "week", "weekNumber", "title"],
                )
                wdata = self._parse_firestore_doc(wdoc)
                sets = self._parse_program_sets(wdata)
                for s in sets:
                    all_exercise_ids.add(s.exercise_id)

                week = None
                for f in ["week", "weekNumber"]:
                    v = wdata.get(f)
                    if isinstance(v, int):
                        week = v
                        break

                workouts.append(ProgramWorkout(
                    id=wid,
                    name=self._extract_localized_name(wdata, "Unnamed Workout"),
                    week=week,
                    sets=sets,
                ))
            except Exception:
                pass

        await self._resolve_exercise_names(all_exercise_ids)
        for w in workouts:
            for s in w.sets:
                s.exercise_name = self._exercises_cache.get(s.exercise_id, s.exercise_id)

        return Program(
            id=program_id,
            name=self._extract_localized_name(program_data, "Unnamed Program"),
            description=program_data.get("description") if isinstance(program_data.get("description"), str) else None,
            workouts_order=workouts_order,
            source=source,
            workouts=workouts,
        )

    @staticmethod
    def _extract_workouts_order(data: dict) -> list[str]:
        wo = data.get("workoutsOrder")
        if isinstance(wo, dict):
            sorted_items = sorted(wo.items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0])
            return [str(v) for _, v in sorted_items]
        if isinstance(wo, list):
            return [str(v) for v in wo]
        return []

    @staticmethod
    def _parse_program_sets(wdata: dict) -> list[ProgramSet]:
        sets_raw = wdata.get("sets")
        if not sets_raw:
            return []

        sets_list: list[tuple[int, dict]] = []

        if isinstance(sets_raw, dict):
            for _, set_data in sets_raw.items():
                if isinstance(set_data, dict):
                    order = set_data.get("order", 0)
                    if not isinstance(order, int):
                        order = 0
                    sets_list.append((order, set_data))
        elif isinstance(sets_raw, list):
            for i, set_data in enumerate(sets_raw):
                if isinstance(set_data, dict):
                    sets_list.append((set_data.get("order", i), set_data))

        sets_list.sort(key=lambda x: x[0])
        result: list[ProgramSet] = []

        for order, sd in sets_list:
            exercise_id = sd.get("exercise")
            if exercise_id is None:
                continue
            exercise_id = str(exercise_id)

            reps = 0
            weight = None
            variables = sd.get("variables")
            if isinstance(variables, dict):
                r = variables.get("reps")
                reps = int(r) if r is not None else 0
                w = variables.get("weight")
                if w is not None:
                    weight = float(w)
            else:
                r = sd.get("reps")
                if r is not None:
                    reps = int(r)

            is_warmup = bool(sd.get("warmup", False))

            result.append(ProgramSet(
                exercise_id=exercise_id,
                order=order,
                reps=reps,
                weight=weight,
                is_warmup=is_warmup,
            ))

        return result

    def get_auth_state(self) -> dict:
        return self._auth.to_dict()

    def restore_auth_state(self, state: dict) -> None:
        self._auth = FirebaseAuth.from_dict(state)
