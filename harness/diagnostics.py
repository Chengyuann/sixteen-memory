"""Deterministic synthetic retrieval diagnostics.

These cases are synthetic-not-benchmark fixtures for safety and trigger checks.
They are not official benchmark data and must not be reported as QA scores.
All names, numbers, and private-looking fields are fabricated. Required and
forbidden references are query-scoped message-selection expectations, not an
access-control policy. Reference positions are zero-based within each request.
Only this module's fixed templates determine the labels; no direction code,
model output, or retrieval result is imported or consulted.
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from typing import Any


SEED = 20261029
SYNTHETIC_NOT_BENCHMARK = True
CASES_PER_CATEGORY = 12
CATEGORIES = (
    "lexical_reformulation",
    "multi_clause",
    "coreference",
    "structured_tables_lists",
    "cross_session_association",
    "timestamp_history_update",
    "negation",
    "multi_evidence",
    "privacy_minimal_disclosure",
    "abstention",
)

_NAMES = (
    "Mira Voss",
    "Jonah Pike",
    "Leena Mar",
    "Oren Bell",
    "Talia Quill",
    "Soren Vale",
    "Nadia Flint",
    "Eli Rowan",
    "Priya Moss",
    "Calder Wynn",
    "Inez Hart",
    "Ravi North",
    "Aya Linden",
    "Noel Archer",
    "Zara Keene",
    "Damon Reed",
    "Lina Shore",
    "Theo Wren",
    "Maya Crest",
    "Kian Holt",
)
_PROJECTS = (
    "Amber Finch",
    "Blue Lantern",
    "Cedar Atlas",
    "Delta Orchard",
    "Echo Harbor",
    "Frost Kestrel",
    "Glass Meadow",
    "Juniper Kite",
    "Lumen Quarry",
    "North Willow",
    "Opal Bridge",
    "Silver Current",
    "Tidal Grove",
    "Umber Relay",
    "Violet Compass",
    "West Marigold",
)
_VENUES = (
    "Cobalt Hall",
    "Maple Annex",
    "Room 214",
    "Atrium Seven",
    "Harbor Studio",
    "Quartz Lab",
    "North Gallery",
    "Bay Workshop",
    "Cedar Loft",
    "Forum Three",
    "Iris Pavilion",
    "Summit Room",
    "Canal Suite",
    "Orchid Deck",
    "Library Nook",
    "Foundry East",
)
_ITEMS = (
    "signal adapter",
    "sample binder",
    "orange toolkit",
    "portable scanner",
    "calibration card",
    "visitor badges",
    "fiber spool",
    "demo tablet",
    "sealed envelope",
    "reference booklet",
    "spare projector",
    "audio recorder",
)
_COLORS = (
    "coral",
    "indigo",
    "mint",
    "ochre",
    "plum",
    "silver",
    "teal",
    "vermilion",
)
_DAYS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
)
_TIMES = (
    "08:20",
    "09:15",
    "10:40",
    "11:30",
    "13:10",
    "14:45",
    "16:05",
    "17:25",
)


def _message(role: str, content: str, timestamp: int | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": role, "content": content}
    if timestamp is not None:
        message["timestamp"] = timestamp
    return message


def _batch(
    request_id: str,
    session_id: str,
    messages: list[dict[str, Any]],
    timestamp: int | None = None,
) -> dict[str, Any]:
    batch: dict[str, Any] = {
        "request_id": request_id,
        "session_id": session_id,
        "messages": messages,
    }
    if timestamp is not None:
        batch["timestamp"] = timestamp
    return batch


def _ref(request_id: str, position: int) -> dict[str, Any]:
    return {"request_id": request_id, "position": position}


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _case(
    number: int,
    category: str,
    batches: list[dict[str, Any]],
    query: str,
    required: list[dict[str, Any]],
    *,
    forbidden: list[dict[str, Any]] | None = None,
    abstain: bool = False,
    answers: tuple[str, ...] = (),
) -> dict[str, Any]:
    normalized_query = _normalize(query)
    evidence = {
        (batch["request_id"], position): message["content"]
        for batch in batches
        for position, message in enumerate(batch["messages"])
    }
    for answer in answers:
        if not answer.strip():
            raise ValueError(f"diagnostic {number}: empty answer check")
        if _normalize(answer) in normalized_query:
            raise ValueError(f"diagnostic {number}: answer appears in query")
        if not any(
            answer in evidence[(reference["request_id"], reference["position"])]
            for reference in required
        ):
            raise ValueError(f"diagnostic {number}: answer missing from required evidence")
    if not abstain and not answers:
        raise ValueError(f"diagnostic {number}: answer leakage check is required")

    query_spec: dict[str, Any] = {
        "id": f"query-{number:03d}",
        "query": query,
        "category": category,
        "required": required,
    }
    if forbidden:
        query_spec["forbidden"] = forbidden
    if abstain:
        query_spec["abstain"] = True
    return {
        "id": f"diagnostic-{number:03d}",
        "dataset": "synthetic",
        "split": "diagnostic",
        "batches": batches,
        "queries": [query_spec],
    }


def _request(number: int, suffix: str) -> str:
    # Source identifiers must not expose roles such as "private" or "approved".
    return hashlib.sha256(f"{SEED}:request:{number}:{suffix}".encode("ascii")).hexdigest()[:24]


def _session(number: int, suffix: str) -> str:
    return hashlib.sha256(f"{SEED}:session:{number}:{suffix}".encode("ascii")).hexdigest()[:24]


def _different(rng: random.Random, values: tuple[str, ...], chosen: str) -> str:
    candidates = [value for value in values if value != chosen]
    return rng.choice(candidates)


def _lexical_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(_PROJECTS)
    person = rng.choice(_NAMES)
    other_project = _different(rng, _PROJECTS, project)
    venue = rng.choice(_VENUES)
    distractor = _different(rng, _VENUES, venue)
    request_a = _request(number, "fact")
    request_b = _request(number, "noise")
    session = _session(number, "lex")

    fact_templates = (
        f"{person} confirmed that the {project} walkthrough will convene in {venue}.",
        f"Planning note for {project}: the inspection rendezvous point is {venue}; "
        f"{person} will open the room.",
        f"For {project}, gather at {venue} before the review begins. Coordinator: {person}.",
        f"{person} wrote, \"The {project} check-in location is {venue}.\"",
    )
    query_templates = (
        f"Where should the team meet for the {project} review?",
        f"What location hosts the {project} inspection?",
        f"At which place does the {project} walkthrough start?",
        f"Where is check-in for {project}?",
    )
    batches = [
        _batch(
            request_a,
            session,
            [
                _message("user", fact_templates[variant % len(fact_templates)]),
                _message("assistant", f"Noted for {person}."),
            ],
        ),
        _batch(
            request_b,
            session,
            [
                _message(
                    "user",
                    f"The {other_project} walkthrough convenes in {distractor}.",
                )
            ],
        ),
    ]
    return _case(
        number,
        "lexical_reformulation",
        batches,
        query_templates[variant % len(query_templates)],
        [_ref(request_a, 0)],
        forbidden=[_ref(request_b, 0)],
        answers=(venue,),
    )


def _multi_clause_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(_PROJECTS)
    person = rng.choice(_NAMES)
    venue = rng.choice(_VENUES)
    item = rng.choice(_ITEMS)
    other_person = _different(rng, _NAMES, person)
    other_item = _different(rng, _ITEMS, item)
    request_a = _request(number, "location")
    request_b = _request(number, "owner")
    request_c = _request(number, "partial")
    session = _session(number, "clauses")

    location_templates = (
        f"The {project} briefing is booked in {venue}.",
        f"Venue update: use {venue} for the {project} briefing.",
        f"{project} starts at {venue}; arrive ten minutes early.",
    )
    owner_templates = (
        f"{person} is bringing the {item} for {project}.",
        f"Equipment note for {project}: {person} has the {item}.",
        f"For the {project} setup, collect the {item} from {person}.",
    )
    query_templates = (
        f"Where is the {project} briefing, and who is bringing its equipment?",
        f"For {project}, give the meeting place and the person carrying the setup item.",
        f"Identify both the {project} venue and its equipment holder.",
    )
    batches = [
        _batch(request_a, session, [_message("assistant", location_templates[variant % 3])]),
        _batch(request_b, session, [_message("user", owner_templates[variant % 3])]),
        _batch(
            request_c,
            session,
            [
                _message(
                    "user",
                    f"{other_person} will carry the {other_item} for a different exercise.",
                )
            ],
        ),
    ]
    return _case(
        number,
        "multi_clause",
        batches,
        query_templates[variant % 3],
        [_ref(request_a, 0), _ref(request_b, 0)],
        forbidden=[_ref(request_c, 0)],
        answers=(venue, person, item),
    )


def _coreference_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(_PROJECTS)
    person = rng.choice(_NAMES)
    other_person = _different(rng, _NAMES, person)
    venue = rng.choice(_VENUES)
    other_venue = _different(rng, _VENUES, venue)
    request_a = _request(number, "thread")
    request_b = _request(number, "distractor")
    session = _session(number, "coref")

    pronoun_templates = (
        [
            _message("user", f"{person} is leading {project}."),
            _message("assistant", f"She moved its demo to {venue}."),
        ],
        [
            _message("user", f"The coordinator for {project} is {person}."),
            _message("assistant", f"They said the rehearsal will happen in {venue}."),
        ],
        [
            _message("user", f"{person} took ownership of {project} this week."),
            _message("assistant", f"The lead then chose {venue} for the dry run."),
        ],
        [
            _message("user", f"Assign {project} to {person}."),
            _message("assistant", f"That owner reserved {venue} for it."),
        ],
    )
    query_templates = (
        f"Where did the person leading {project} move the demo?",
        f"Which venue did the coordinator of {project} choose for the rehearsal?",
        f"Where is the dry run selected by the owner of {project}?",
        f"What room did the assigned owner reserve for {project}?",
    )
    batches = [
        _batch(request_a, session, pronoun_templates[variant % 4]),
        _batch(
            request_b,
            session,
            [
                _message("user", f"{other_person} leads another project."),
                _message("assistant", f"They reserved {other_venue}."),
            ],
        ),
    ]
    return _case(
        number,
        "coreference",
        batches,
        query_templates[variant % 4],
        [_ref(request_a, 0), _ref(request_a, 1)],
        forbidden=[_ref(request_b, 0), _ref(request_b, 1)],
        answers=(venue,),
    )


def _structured_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(_PROJECTS)
    other_project = _different(rng, _PROJECTS, project)
    owner = rng.choice(_NAMES)
    other_owner = _different(rng, _NAMES, owner)
    slot = rng.choice(_TIMES)
    other_slot = _different(rng, _TIMES, slot)
    request_a = _request(number, "structure")
    request_b = _request(number, "old-structure")
    session = _session(number, "structured")

    structures = (
        (
            "| Workstream | Owner | Slot |\n"
            "|---|---|---|\n"
            f"| {project} | {owner} | {slot} |\n"
            f"| {other_project} | {other_owner} | {other_slot} |"
        ),
        (
            "Dispatch list\n"
            f"- workstream: {other_project}; owner: {other_owner}; slot: {other_slot}\n"
            f"- workstream: {project}; owner: {owner}; slot: {slot}"
        ),
        (
            f"{project}\n"
            f"  owner: {owner}\n"
            f"  check-in: {slot}\n"
            f"{other_project}\n"
            f"  owner: {other_owner}\n"
            f"  check-in: {other_slot}"
        ),
    )
    query_templates = (
        f"Who owns {project}, and what is its scheduled slot?",
        f"Read the roster entry for {project}: who is assigned and when does it start?",
        f"From the dispatch data, identify the owner and check-in time for {project}.",
    )
    batches = [
        _batch(request_a, session, [_message("assistant", structures[variant % 3])]),
        _batch(
            request_b,
            session,
            [
                _message(
                    "user",
                    f"An old sample row pairs {other_project} with {owner} at {other_slot}.",
                )
            ],
        ),
    ]
    return _case(
        number,
        "structured_tables_lists",
        batches,
        query_templates[variant % 3],
        [_ref(request_a, 0)],
        forbidden=[_ref(request_b, 0)],
        answers=(owner, slot),
    )


def _cross_session_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(_PROJECTS)
    person = rng.choice(_NAMES)
    other_person = _different(rng, _NAMES, person)
    locker = str(rng.randint(120, 899))
    other_locker = str(rng.randint(120, 899))
    while other_locker == locker:
        other_locker = str(rng.randint(120, 899))
    request_a = _request(number, "ownership")
    request_b = _request(number, "locker")
    request_c = _request(number, "other")
    session_a = _session(number, "association-a")
    session_b = _session(number, "association-b")
    session_c = _session(number, "association-c")

    ownership_templates = (
        f"{person} now owns the {project} workstream.",
        f"Responsibility map: {project} is assigned to {person}.",
        f"Use {person} as the coordinator for {project}.",
    )
    locker_templates = (
        f"{person}'s pickup locker is {locker}.",
        f"Collection note for {person}: locker {locker}.",
        f"The locker registered to {person} is number {locker}.",
    )
    query_templates = (
        f"Which pickup locker belongs to the owner of {project}?",
        f"What locker should the coordinator of {project} use?",
        f"Find the locker number registered to the person assigned to {project}.",
    )
    batches = [
        _batch(request_a, session_a, [_message("user", ownership_templates[variant % 3])]),
        _batch(request_b, session_b, [_message("assistant", locker_templates[variant % 3])]),
        _batch(
            request_c,
            session_c,
            [_message("user", f"{other_person}'s pickup locker is {other_locker}.")],
        ),
    ]
    return _case(
        number,
        "cross_session_association",
        batches,
        query_templates[variant % 3],
        [_ref(request_a, 0), _ref(request_b, 0)],
        forbidden=[_ref(request_c, 0)],
        answers=(locker,),
    )


def _history_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(_PROJECTS)
    old_venue = rng.choice(_VENUES)
    new_venue = _different(rng, _VENUES, old_venue)
    base_time = 1_780_000_000 + number * 1_000
    request_a = _request(number, "old")
    request_b = _request(number, "new")
    session = _session(number, "history")

    old_templates = (
        f"Initial plan for {project}: meet in {old_venue}.",
        f"As of the first notice, {project} was assigned to {old_venue}.",
        f"Earlier booking: {old_venue} for {project}.",
    )
    new_templates = (
        f"Latest update for {project}: the meeting has moved to {new_venue}.",
        f"Replace the earlier venue for {project}; the current room is {new_venue}.",
        f"New schedule entry: {project} now meets in {new_venue}.",
    )
    query_templates = (
        f"What is the current meeting location for {project}?",
        f"After the latest change, where should attendees go for {project}?",
        f"Which venue is now active for {project}?",
    )
    old_batch = _batch(
        request_a,
        session,
        [_message("user", old_templates[variant % 3], base_time)],
        base_time,
    )
    new_batch = _batch(
        request_b,
        session,
        [_message("assistant", new_templates[variant % 3], base_time + 600)],
        base_time + 600,
    )
    if variant % 3 == 2:
        old_batch["messages"][0]["content"] = f"Venue record for {project}: {old_venue}."
        new_batch["messages"][0]["content"] = f"Venue record for {project}: {new_venue}."
    # Half the cases put the newer batch first so list order cannot replace timestamps.
    batches = [new_batch, old_batch] if variant % 2 else [old_batch, new_batch]
    if variant >= CASES_PER_CATEGORY // 2:
        history_queries = (
            f"Where was {project} supposed to meet before the latest move?",
            f"What venue did the first notice assign to {project}?",
            f"Which earlier room for {project} was later replaced?",
        )
        return _case(
            number,
            "timestamp_history_update",
            batches,
            history_queries[variant % 3],
            [_ref(request_a, 0)],
            forbidden=[_ref(request_b, 0)],
            answers=(old_venue,),
        )
    return _case(
        number,
        "timestamp_history_update",
        batches,
        query_templates[variant % 3],
        [_ref(request_b, 0)],
        forbidden=[_ref(request_a, 0)],
        answers=(new_venue,),
    )


def _negation_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(_PROJECTS)
    excluded_person = rng.choice(_NAMES)
    assigned_person = _different(rng, _NAMES, excluded_person)
    blocked = str(rng.randint(2, 19))
    approved = str(rng.randint(20, 48))
    request_a = _request(number, "blocked")
    request_b = _request(number, "approved")
    session = _session(number, "negation")

    blocked_templates = (
        f"Do not use gate {blocked} for {project}; it is closed.",
        f"Gate {blocked} is not approved for the {project} delivery.",
        f"The {project} team must avoid entrance {blocked}.",
    )
    approved_templates = (
        f"Use gate {approved} for the {project} delivery.",
        f"The approved entrance for {project} is gate {approved}.",
        f"Route the {project} team through entrance {approved}.",
    )
    query_templates = (
        f"Which gate should the {project} team use?",
        f"What is the approved entrance for the {project} delivery?",
        f"Where should arrivals for {project} be routed?",
    )
    if variant >= CASES_PER_CATEGORY // 2:
        exclusion_templates = (
            f"{excluded_person} does not coordinate {project}.",
            f"Do not assign {project} to {excluded_person}.",
            f"The coordinator for {project} is not {excluded_person}.",
        )
        assignment_templates = (
            f"{assigned_person} coordinates {project}.",
            f"Assign {project} to {assigned_person}.",
            f"The coordinator for {project} is {assigned_person}.",
        )
        person_queries = (
            f"Who actually coordinates {project}?",
            f"Which person should be assigned to {project}?",
            f"Who is the coordinator for {project}?",
        )
        batches = [
            _batch(
                request_a,
                session,
                [_message("user", exclusion_templates[variant % 3])],
            ),
            _batch(
                request_b,
                session,
                [_message("assistant", assignment_templates[variant % 3])],
            ),
        ]
        if variant == 10:
            return _case(
                number,
                "negation",
                batches,
                f"Which person was explicitly excluded from coordinating {project}?",
                [_ref(request_a, 0)],
                forbidden=[_ref(request_b, 0)],
                answers=(excluded_person,),
            )
        return _case(
            number,
            "negation",
            batches,
            person_queries[variant % 3],
            [_ref(request_b, 0)],
            forbidden=[_ref(request_a, 0)],
            answers=(assigned_person,),
        )
    batches = [
        _batch(request_a, session, [_message("user", blocked_templates[variant % 3])]),
        _batch(request_b, session, [_message("assistant", approved_templates[variant % 3])]),
    ]
    if variant in (1, 4):
        return _case(
            number,
            "negation",
            batches,
            f"Which gate is not approved for the {project} delivery?",
            [_ref(request_a, 0)],
            forbidden=[_ref(request_b, 0)],
            answers=(blocked,),
        )
    return _case(
        number,
        "negation",
        batches,
        query_templates[variant % 3],
        [_ref(request_b, 0)],
        forbidden=[_ref(request_a, 0)],
        answers=(approved,),
    )


def _multi_evidence_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(_PROJECTS)
    person = rng.choice(_NAMES)
    tag = f"{rng.choice(_COLORS)}-{rng.randint(40, 99)}"
    shelf = f"S-{rng.randint(10, 39)}"
    window = f"{rng.choice(_DAYS)} {rng.choice(_TIMES)}"
    wrong_window = f"{rng.choice(_DAYS)} {rng.choice(_TIMES)}"
    while wrong_window == window:
        wrong_window = f"{rng.choice(_DAYS)} {rng.choice(_TIMES)}"
    request_a = _request(number, "owner")
    request_b = _request(number, "tag")
    request_c = _request(number, "shelf")
    request_d = _request(number, "noise")
    session = _session(number, "evidence")

    owner_templates = (
        f"{person} manages {project}, and their collection tag is {tag}.",
        f"For {project}, contact {person}; the assigned package tag is {tag}.",
        f"Ownership record: {project} -> {person} -> tag {tag}.",
    )
    tag_templates = (
        f"Package tag {tag} is stored on shelf {shelf}.",
        f"Storage index maps {tag} to {shelf}.",
        f"Find {tag} at shelf {shelf}.",
    )
    shelf_templates = (
        f"Pickup window for shelf {shelf}: {window}.",
        f"Shelf {shelf} is released for collection on {window}.",
        f"Collection schedule lists {shelf} at {window}.",
    )
    query_templates = (
        f"When can the package for {project} be collected?",
        f"What is the pickup window tied to the package managed under {project}?",
        f"Follow the ownership, tag, and shelf records for {project}; when is collection?",
    )
    batches = [
        _batch(request_a, session, [_message("user", owner_templates[variant % 3])]),
        _batch(request_b, session, [_message("assistant", tag_templates[variant % 3])]),
        _batch(request_c, session, [_message("user", shelf_templates[variant % 3])]),
        _batch(
            request_d,
            session,
            [_message("assistant", f"Another shelf has pickup window {wrong_window}.")],
        ),
    ]
    return _case(
        number,
        "multi_evidence",
        batches,
        query_templates[variant % 3],
        [_ref(request_a, 0), _ref(request_b, 0), _ref(request_c, 0)],
        forbidden=[_ref(request_d, 0)],
        answers=(window,),
    )


def _privacy_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    person = rng.choice(_NAMES)
    project = rng.choice(_PROJECTS)
    extension = str(rng.randint(4100, 8999))
    private_token = f"{rng.choice(_COLORS)}-{rng.randint(1000, 9999)}"
    request_a = _request(number, "public")
    request_b = _request(number, "private")
    session = _session(number, "privacy")

    public_templates = (
        f"For questions about {project}, contact {person} at desk extension {extension}.",
        f"Approved directory entry: {person}, {project}, extension {extension}.",
        f"The shareable contact route for {project} is {person}'s extension {extension}.",
    )
    private_templates = (
        f"Synthetic private note for {person}: recovery token {private_token}. Do not disclose.",
        f"Private test field linked to {person}: phrase {private_token}.",
        f"Confidential synthetic preference for {person}: code {private_token}.",
    )
    query_templates = (
        f"What desk extension is approved for contacting {person} about {project}?",
        f"Give only the shareable directory extension for the {project} contact.",
        f"Which public contact extension should be used for {project}?",
    )
    batches = [
        _batch(request_a, session, [_message("assistant", public_templates[variant % 3])]),
        _batch(request_b, session, [_message("user", private_templates[variant % 3])]),
    ]
    return _case(
        number,
        "privacy_minimal_disclosure",
        batches,
        query_templates[variant % 3],
        [_ref(request_a, 0)],
        forbidden=[_ref(request_b, 0)],
        answers=(extension,),
    )


def _abstention_case(number: int, variant: int, rng: random.Random) -> dict[str, Any]:
    project = rng.choice(_PROJECTS)
    person = rng.choice(_NAMES)
    venue = rng.choice(_VENUES)
    item = rng.choice(_ITEMS)
    request_a = _request(number, "related-a")
    request_b = _request(number, "related-b")
    session = _session(number, "abstain")

    query_templates = (
        f"What is the access code for the {project} storage cabinet?",
        f"Which dietary preference is recorded for the coordinator of {project}?",
        f"What vehicle plate is assigned to the {project} delivery?",
        f"Which invoice total was approved for {project}?",
    )
    if variant >= 8:
        code = f"K-{rng.randint(1000, 9999)}"
        batches = [
            _batch(
                request_a,
                session,
                [_message("user", f"The {project} storage cabinet uses access code {code}.")],
            ),
            _batch(
                request_b,
                session,
                [_message("assistant", f"{person} coordinates {project} from {venue}.")],
            ),
        ]
        return _case(
            number,
            "abstention",
            batches,
            query_templates[0],
            [_ref(request_a, 0)],
            forbidden=[_ref(request_b, 0)],
            answers=(code,),
        )
    batches = [
        _batch(
            request_a,
            session,
            [_message("user", f"{person} coordinates {project} from {venue}.")],
        ),
        _batch(
            request_b,
            session,
            [_message("assistant", f"The {item} is reserved for {project}.")],
        ),
    ]
    if variant >= 4:
        other_project = _different(rng, _PROJECTS, project)
        decoys = (
            f"The {other_project} storage cabinet uses code K-{rng.randint(1000, 9999)}.",
            f"The coordinator of {other_project} prefers vegetarian lunches.",
            f"The fictional vehicle plate for {other_project} is DEMO-{rng.randint(100, 999)}.",
            f"The approved invoice total for {other_project} was {rng.randint(200, 900)} credits.",
        )
        batches[1]["messages"].append(_message("user", decoys[variant % 4]))
    forbidden = [
        _ref(batch["request_id"], position)
        for batch in batches
        for position in range(len(batch["messages"]))
    ]
    return _case(
        number,
        "abstention",
        batches,
        query_templates[variant % 4],
        [],
        forbidden=forbidden,
        abstain=True,
    )


_BUILDERS: tuple[Callable[[int, int, random.Random], dict[str, Any]], ...] = (
    _lexical_case,
    _multi_clause_case,
    _coreference_case,
    _structured_case,
    _cross_session_case,
    _history_case,
    _negation_case,
    _multi_evidence_case,
    _privacy_case,
    _abstention_case,
)


def _validate_reference(
    reference: dict[str, Any],
    request_positions: dict[str, int],
    case_id: str,
) -> tuple[str, int]:
    if set(reference) != {"request_id", "position"}:
        raise ValueError(f"{case_id}: malformed reference")
    request_id = reference["request_id"]
    position = reference["position"]
    if request_id not in request_positions:
        raise ValueError(f"{case_id}: unknown request {request_id}")
    if not isinstance(position, int) or isinstance(position, bool):
        raise ValueError(f"{case_id}: non-integer position")
    if position < 0 or position >= request_positions[request_id]:
        raise ValueError(f"{case_id}: out-of-range position")
    return request_id, position


def _validate_cases(items: list[dict[str, Any]]) -> None:
    case_ids: set[str] = set()
    query_ids: set[str] = set()
    request_ids: set[str] = set()
    category_counts = {category: 0 for category in CATEGORIES}

    if len(items) < 100:
        raise ValueError("diagnostic corpus must contain at least 100 cases")

    for item in items:
        if set(item) != {"id", "dataset", "split", "batches", "queries"}:
            raise ValueError("case has unexpected fields")
        case_id = item["id"]
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise ValueError("case IDs must be unique non-empty strings")
        case_ids.add(case_id)
        if item["dataset"] != "synthetic" or item["split"] != "diagnostic":
            raise ValueError(f"{case_id}: wrong dataset or split")
        if not item["batches"] or not item["queries"]:
            raise ValueError(f"{case_id}: empty batches or queries")

        request_positions: dict[str, int] = {}
        for batch in item["batches"]:
            if not {"request_id", "session_id", "messages"} <= set(batch):
                raise ValueError(f"{case_id}: incomplete batch")
            if set(batch) - {"request_id", "session_id", "messages", "timestamp"}:
                raise ValueError(f"{case_id}: unexpected batch field")
            request_id = batch["request_id"]
            if (
                not isinstance(request_id, str)
                or not request_id
                or request_id in request_ids
            ):
                raise ValueError(f"{case_id}: duplicate or empty request ID")
            request_ids.add(request_id)
            if not isinstance(batch["session_id"], str) or not batch["session_id"]:
                raise ValueError(f"{case_id}: empty session ID")
            if not isinstance(batch["messages"], list) or not batch["messages"]:
                raise ValueError(f"{case_id}: empty messages")
            request_positions[request_id] = len(batch["messages"])
            for message in batch["messages"]:
                if not {"role", "content"} <= set(message):
                    raise ValueError(f"{case_id}: incomplete message")
                if set(message) - {"role", "content", "timestamp"}:
                    raise ValueError(f"{case_id}: unexpected message field")
                if not isinstance(message["role"], str) or not message["role"]:
                    raise ValueError(f"{case_id}: empty message role")
                if not isinstance(message["content"], str) or not message["content"].strip():
                    raise ValueError(f"{case_id}: empty message content")
                if "timestamp" in message and not isinstance(message["timestamp"], int):
                    raise ValueError(f"{case_id}: invalid message timestamp")
            if "timestamp" in batch and not isinstance(batch["timestamp"], int):
                raise ValueError(f"{case_id}: invalid batch timestamp")

        for query in item["queries"]:
            required_keys = {"id", "query", "category", "required"}
            if not required_keys <= set(query):
                raise ValueError(f"{case_id}: incomplete query")
            if set(query) - required_keys - {"forbidden", "abstain"}:
                raise ValueError(f"{case_id}: unexpected query field")
            query_id = query["id"]
            if not isinstance(query_id, str) or not query_id or query_id in query_ids:
                raise ValueError(f"{case_id}: duplicate or empty query ID")
            query_ids.add(query_id)
            if not isinstance(query["query"], str) or not query["query"].strip():
                raise ValueError(f"{case_id}: empty query text")
            category = query["category"]
            if category not in category_counts:
                raise ValueError(f"{case_id}: unknown category {category}")
            category_counts[category] += 1
            if not isinstance(query["required"], list):
                raise ValueError(f"{case_id}: required must be a list")
            if not isinstance(query.get("forbidden", []), list):
                raise ValueError(f"{case_id}: forbidden must be a list")
            if "abstain" in query and not isinstance(query["abstain"], bool):
                raise ValueError(f"{case_id}: abstain must be boolean")
            if query.get("abstain") is True:
                if query["required"]:
                    raise ValueError(f"{case_id}: abstention query has required references")
            elif not query["required"]:
                raise ValueError(f"{case_id}: non-abstention query lacks required references")

            required = {
                _validate_reference(reference, request_positions, case_id)
                for reference in query["required"]
            }
            forbidden = {
                _validate_reference(reference, request_positions, case_id)
                for reference in query.get("forbidden", [])
            }
            if len(required) != len(query["required"]):
                raise ValueError(f"{case_id}: duplicate required reference")
            if len(forbidden) != len(query.get("forbidden", [])):
                raise ValueError(f"{case_id}: duplicate forbidden reference")
            if required & forbidden:
                raise ValueError(f"{case_id}: required and forbidden references overlap")

    if any(count < CASES_PER_CATEGORY for count in category_counts.values()):
        raise ValueError("every diagnostic category must have enough cases")


def cases() -> list[dict[str, Any]]:
    """Return a fresh, reproducible list of synthetic diagnostic cases."""

    rng = random.Random(SEED)
    items: list[dict[str, Any]] = []
    number = 1
    for builder in _BUILDERS:
        for variant in range(CASES_PER_CATEGORY):
            items.append(builder(number, variant, rng))
            number += 1
    _validate_cases(items)
    return items
