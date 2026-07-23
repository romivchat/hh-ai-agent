import asyncio
import hashlib
import difflib
import json
import os
import re
from pathlib import Path

import aiohttp
from config import (
    APPLICANT_NAME,
    CANDIDATE_PROFILE_PATH,
    OLLAMA_CONTEXT_LENGTH,
    OLLAMA_MODEL,
    OLLAMA_URL,
)


LETTER_VERSION = 2
MIN_LETTER_WORDS = 130
MAX_LETTER_WORDS = 190

CAPABILITIES = (
    "strategy",
    "discovery",
    "delivery",
    "growth",
    "activation",
    "retention",
    "monetization",
    "portfolio",
    "credit",
    "b2b",
    "fintech",
    "launch",
    "experiments",
    "analytics",
    "team",
    "stakeholders",
    "integrations",
    "release_management",
    "agile",
    "ci_cd",
    "crm",
    "risk",
    "hardware",
    "suppliers",
    "ai",
)

ROLE_FAMILIES = (
    "product",
    "product_leadership",
    "release_delivery",
    "project",
    "sales",
    "engineering",
    "other",
)

ITEM_KINDS = (
    "task",
    "must_have",
    "nice_to_have",
    "context",
    "language",
    "geography",
    "cover_letter_request",
)

REQUEST_KINDS = ("compensation", "language", "portfolio", "other")
MATCH_STATUSES = ("direct", "transferable", "gap", "unknown")

STRICT_CAPABILITIES = {
    "activation",
    "retention",
    "crm",
    "risk",
    "integrations",
    "release_management",
    "agile",
    "ci_cd",
    "hardware",
    "suppliers",
}

LEGACY_CAPABILITY_MAP = {
    "strategy": ["strategy"],
    "discovery": ["discovery"],
    "growth": ["growth"],
    "monetization": ["monetization"],
    "launch": ["launch"],
    "b2b": ["b2b"],
    "fintech": ["fintech"],
    "team": ["team", "stakeholders"],
    "process": ["delivery", "agile"],
    "ai": ["ai"],
}

RISKY_PHRASES = (
    "на senior-уровне",
    "тёмные паттерны",
    "темные паттерны",
    "идеально подхожу",
)


class OllamaUnavailableError(RuntimeError):
    """Ollama недоступна или вернула ответ, который нельзя использовать."""


class CandidateProfileError(RuntimeError):
    """Закрытый профиль кандидата отсутствует или заполнен неверно."""


async def _ask_ollama(
    prompt: str,
    timeout_seconds: int = 60,
    temperature: float = 0.1,
    max_output_tokens: int = 768,
    response_format=None,
) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_output_tokens,
            "num_ctx": OLLAMA_CONTEXT_LENGTH,
        },
    }
    if response_format is not None:
        payload["format"] = response_format

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OLLAMA_URL, json=payload) as response:
                response.raise_for_status()
                data = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        raise OllamaUnavailableError(
            f"Ollama недоступна по адресу {OLLAMA_URL}: {error}"
        ) from error

    answer = data.get("response", "").strip()
    if not answer:
        raise OllamaUnavailableError("Ollama вернула пустой ответ.")
    return answer


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _normalise_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise CandidateProfileError("Профиль кандидата должен быть объектом")

    legacy_positioning = profile.get("positioning")
    positionings = profile.get("positionings")
    if not isinstance(positionings, list) or not positionings:
        if not isinstance(legacy_positioning, str) or not legacy_positioning.strip():
            raise CandidateProfileError("В профиле не заполнено позиционирование")
        positionings = [
            {
                "id": "general",
                "capabilities": [],
                "domains": [],
                "text": legacy_positioning.strip(),
            }
        ]

    normalized_positionings = []
    positioning_ids = set()
    for item in positionings:
        if not isinstance(item, dict):
            raise CandidateProfileError("Каждое позиционирование должно быть объектом")
        item_id = item.get("id")
        text = item.get("text")
        if not isinstance(item_id, str) or not item_id or item_id in positioning_ids:
            raise CandidateProfileError("ID позиционирований должны быть уникальными")
        if not isinstance(text, str) or not text.strip():
            raise CandidateProfileError(f"Не заполнено позиционирование: {item_id}")
        capabilities = item.get("capabilities", [])
        if not isinstance(capabilities, list) or any(
            capability not in CAPABILITIES for capability in capabilities
        ):
            raise CandidateProfileError(f"Неверные capabilities: {item_id}")
        domains = item.get("domains", [])
        if not isinstance(domains, list) or any(
            not isinstance(domain, str) or not domain for domain in domains
        ):
            raise CandidateProfileError(f"Неверные domains: {item_id}")
        normalized_positionings.append(
            {
                "id": item_id,
                "capabilities": _deduplicate(capabilities),
                "domains": _deduplicate(domains),
                "text": text.strip(),
            }
        )
        positioning_ids.add(item_id)

    facts = profile.get("facts")
    if not isinstance(facts, list) or len(facts) < 2:
        raise CandidateProfileError("В профиле должно быть минимум два факта")

    normalized_facts = []
    fact_ids = set()
    for fact in facts:
        if not isinstance(fact, dict):
            raise CandidateProfileError("Каждый факт должен быть объектом")
        fact_id = fact.get("id")
        text = fact.get("public_text", fact.get("text"))
        capabilities = fact.get("capabilities")
        if capabilities is None:
            capabilities = []
            for focus_id in fact.get("focus_ids", []):
                capabilities.extend(LEGACY_CAPABILITY_MAP.get(focus_id, []))
        if not isinstance(fact_id, str) or not fact_id or fact_id in fact_ids:
            raise CandidateProfileError("ID фактов должны быть уникальными")
        if not isinstance(text, str) or not text.strip():
            raise CandidateProfileError(f"Не заполнен публичный текст факта: {fact_id}")
        if not isinstance(capabilities, list) or not capabilities or any(
            capability not in CAPABILITIES for capability in capabilities
        ):
            raise CandidateProfileError(f"Неверные capabilities факта: {fact_id}")
        domains = fact.get("domains", [])
        if not isinstance(domains, list) or any(
            not isinstance(domain, str) or not domain for domain in domains
        ):
            raise CandidateProfileError(f"Неверные domains факта: {fact_id}")
        normalized_facts.append(
            {
                "id": fact_id,
                "capabilities": _deduplicate(capabilities),
                "domains": _deduplicate(domains),
                "public_text": text.strip(),
            }
        )
        fact_ids.add(fact_id)

    forbidden_terms = profile.get("forbidden_terms", [])
    if not isinstance(forbidden_terms, list) or any(
        not isinstance(term, str) or not term for term in forbidden_terms
    ):
        raise CandidateProfileError("forbidden_terms должен быть списком строк")

    preferences = profile.get("preferences", {})
    verified = profile.get("verified", {})
    if not isinstance(preferences, dict) or not isinstance(verified, dict):
        raise CandidateProfileError("preferences и verified должны быть объектами")

    screening_answers = profile.get("screening_answers", [])
    if not isinstance(screening_answers, list):
        raise CandidateProfileError("screening_answers должен быть списком")
    normalized_screening_answers = []
    screening_answer_ids = set()
    for item in screening_answers:
        if not isinstance(item, dict):
            raise CandidateProfileError("Каждый ответ работодателю должен быть объектом")
        item_id = item.get("id")
        question_terms = item.get("question_terms")
        answer = item.get("answer")
        if (
            not isinstance(item_id, str)
            or not item_id
            or item_id in screening_answer_ids
        ):
            raise CandidateProfileError("ID ответов работодателю должны быть уникальными")
        if not isinstance(question_terms, list) or not question_terms or any(
            not isinstance(term, str) or not term.strip() for term in question_terms
        ):
            raise CandidateProfileError(
                f"Не заполнены ключевые слова вопроса работодателя: {item_id}"
            )
        if not isinstance(answer, str) or not answer.strip():
            raise CandidateProfileError(
                f"Не заполнен ответ на вопрос работодателя: {item_id}"
            )
        normalized_screening_answers.append(
            {
                "id": item_id,
                "question_terms": [term.strip() for term in question_terms],
                "answer": answer.strip(),
            }
        )
        screening_answer_ids.add(item_id)

    return {
        "schema_version": 2,
        "positionings": normalized_positionings,
        "facts": normalized_facts,
        "forbidden_terms": forbidden_terms,
        "preferences": preferences,
        "verified": verified,
        "screening_answers": normalized_screening_answers,
    }


def _load_candidate_profile() -> dict:
    path = Path(CANDIDATE_PROFILE_PATH)
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise CandidateProfileError(f"Не найден профиль кандидата: {path}") from error
    except (json.JSONDecodeError, OSError) as error:
        raise CandidateProfileError(f"Не удалось прочитать профиль: {path}") from error
    return _normalise_profile(profile)


def _normalise_question_text(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def find_screening_answer(question: str) -> str | None:
    normalized_question = _normalise_question_text(question)
    for item in _load_candidate_profile()["screening_answers"]:
        terms = [
            _normalise_question_text(term) for term in item["question_terms"]
        ]
        if all(term in normalized_question for term in terms):
            return item["answer"]
    return None


def _analysis_schema(profile: dict) -> dict:
    fact_ids = [fact["id"] for fact in profile["facts"]]
    positioning_ids = [item["id"] for item in profile["positionings"]]
    evidence_item = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "kind": {"type": "string", "enum": list(ITEM_KINDS)},
            "text": {"type": "string"},
            "evidence": {"type": "string"},
            "capabilities": {
                "type": "array",
                "items": {"type": "string", "enum": list(CAPABILITIES)},
                "uniqueItems": True,
            },
            "request_kind": {
                "type": "string",
                "enum": list(REQUEST_KINDS),
            },
        },
        "required": ["id", "kind", "text", "evidence", "capabilities", "request_kind"],
    }
    match_item = {
        "type": "object",
        "properties": {
            "requirement_id": {"type": "string"},
            "status": {"type": "string", "enum": list(MATCH_STATUSES)},
            "fact_ids": {
                "type": "array",
                "items": {"type": "string", "enum": fact_ids},
                "uniqueItems": True,
            },
        },
        "required": ["requirement_id", "status", "fact_ids"],
    }
    return {
        "type": "object",
        "properties": {
            "suitable": {"type": "boolean"},
            "role_family": {"type": "string", "enum": list(ROLE_FAMILIES)},
            "role_summary": {"type": "string"},
            "primary_goal": evidence_item,
            "items": {"type": "array", "items": evidence_item, "maxItems": 16},
            "matches": {"type": "array", "items": match_item, "maxItems": 12},
            "relevance": {"type": "string", "enum": ["high", "medium", "low"]},
            "positioning_id": {"type": "string", "enum": positioning_ids},
            "selected_fact_ids": {
                "type": "array",
                "items": {"type": "string", "enum": fact_ids},
                "minItems": 2,
                "maxItems": 2,
                "uniqueItems": True,
            },
        },
        "required": [
            "suitable",
            "role_family",
            "role_summary",
            "primary_goal",
            "items",
            "matches",
            "relevance",
            "positioning_id",
            "selected_fact_ids",
        ],
    }


def _profile_catalog(profile: dict) -> str:
    positionings = "\n".join(
        f"- {item['id']}: {item['text']} | capabilities={item['capabilities']} | domains={item['domains']}"
        for item in profile["positionings"]
    )
    facts = "\n".join(
        f"- {fact['id']}: {fact['public_text']} | capabilities={fact['capabilities']} | domains={fact['domains']}"
        for fact in profile["facts"]
    )
    return f"ПОЗИЦИОНИРОВАНИЯ:\n{positionings}\n\nПОДТВЕРЖДЁННЫЕ ФАКТЫ:\n{facts}"


def _validate_evidence_item(item: dict, vacancy_description: str) -> None:
    required = {"id", "kind", "text", "evidence", "capabilities", "request_kind"}
    if not isinstance(item, dict) or set(item) != required:
        raise OllamaUnavailableError("Ollama вернула неполный пункт анализа")
    if item["kind"] not in ITEM_KINDS or item["request_kind"] not in REQUEST_KINDS:
        raise OllamaUnavailableError("Ollama вернула неизвестный тип требования")
    if not all(isinstance(item[field], str) and item[field].strip() for field in ("id", "text", "evidence")):
        raise OllamaUnavailableError("Ollama вернула пустой пункт анализа")
    if item["evidence"].casefold() not in vacancy_description.casefold():
        raise OllamaUnavailableError(
            f"Требование не подтверждено текстом вакансии: {item['text']}"
        )
    if not isinstance(item["capabilities"], list) or any(
        value not in CAPABILITIES for value in item["capabilities"]
    ):
        raise OllamaUnavailableError("Ollama вернула неизвестную компетенцию")


def _validate_analysis(result: dict, vacancy_description: str, profile: dict) -> dict:
    required = set(_analysis_schema(profile)["required"])
    if not isinstance(result, dict) or set(result) != required:
        raise OllamaUnavailableError("Ollama вернула неполную карточку вакансии")
    if not isinstance(result["suitable"], bool):
        raise OllamaUnavailableError("Ollama вернула неверный suitable")
    if result["role_family"] not in ROLE_FAMILIES or result["relevance"] not in {"high", "medium", "low"}:
        raise OllamaUnavailableError("Ollama вернула неверную классификацию роли")
    if not isinstance(result["role_summary"], str) or not result["role_summary"].strip():
        raise OllamaUnavailableError("Ollama не описала фактическую роль")

    _validate_evidence_item(result["primary_goal"], vacancy_description)
    if not isinstance(result["items"], list) or len(result["items"]) > 16:
        raise OllamaUnavailableError("Ollama вернула неверный список требований")
    item_ids = {result["primary_goal"]["id"]}
    for item in result["items"]:
        _validate_evidence_item(item, vacancy_description)
        if item["id"] in item_ids:
            raise OllamaUnavailableError("Ollama повторила ID требования")
        item_ids.add(item["id"])

    fact_ids = {fact["id"] for fact in profile["facts"]}
    if result["positioning_id"] not in {item["id"] for item in profile["positionings"]}:
        raise OllamaUnavailableError("Ollama выбрала неизвестное позиционирование")
    selected = result["selected_fact_ids"]
    if not isinstance(selected, list) or len(selected) != 2 or len(selected) != len(set(selected)) or any(
        fact_id not in fact_ids for fact_id in selected
    ):
        raise OllamaUnavailableError("Ollama выбрала неизвестные факты")

    if not isinstance(result["matches"], list):
        raise OllamaUnavailableError("Ollama вернула неверные совпадения")
    matched_requirement_ids = set()
    for match in result["matches"]:
        if not isinstance(match, dict) or set(match) != {"requirement_id", "status", "fact_ids"}:
            raise OllamaUnavailableError("Ollama вернула неполное совпадение")
        if match["requirement_id"] not in item_ids or match["status"] not in MATCH_STATUSES:
            raise OllamaUnavailableError("Ollama сопоставила неизвестное требование")
        if match["requirement_id"] in matched_requirement_ids:
            raise OllamaUnavailableError("Ollama повторила сопоставление требования")
        matched_requirement_ids.add(match["requirement_id"])
        if not isinstance(match["fact_ids"], list) or any(
            fact_id not in fact_ids for fact_id in match["fact_ids"]
        ):
            raise OllamaUnavailableError("Ollama сослалась на неизвестный факт")
        if match["status"] in {"direct", "transferable"} and not match["fact_ids"]:
            match["status"] = "unknown"
        if match["status"] in {"gap", "unknown"} and match["fact_ids"]:
            match["fact_ids"] = []

    candidate_items = [result["primary_goal"], *result["items"]]
    required_match_ids = {
        item["id"]
        for item in candidate_items
        if item["kind"] in {"task", "must_have", "language"}
    }
    for requirement_id in sorted(required_match_ids - matched_requirement_ids):
        result["matches"].append(
            {
                "requirement_id": requirement_id,
                "status": "unknown",
                "fact_ids": [],
            }
        )

    items = _item_map(result)
    facts = {fact["id"]: fact for fact in profile["facts"]}
    for match in result["matches"]:
        item = items[match["requirement_id"]]
        if item["kind"] == "language" and not profile["verified"].get("english"):
            match["status"] = "unknown"
            match["fact_ids"] = []
            continue
        strict_required = set(item["capabilities"]) & STRICT_CAPABILITIES
        supported_capabilities = {
            capability
            for fact_id in match["fact_ids"]
            for capability in facts[fact_id]["capabilities"]
        }
        missing_strict = strict_required - supported_capabilities
        if missing_strict:
            if strict_required & supported_capabilities and match["status"] == "direct":
                match["status"] = "transferable"
            else:
                match["status"] = "gap"
                match["fact_ids"] = []
        elif (
            "portfolio" in item["capabilities"]
            and match["status"] == "direct"
            and "portfolio" not in supported_capabilities
        ):
            match["status"] = (
                "transferable"
                if supported_capabilities & {"credit", "monetization"}
                else "gap"
            )
            if match["status"] == "gap":
                match["fact_ids"] = []

    direct_facts = {
        fact_id
        for match in result["matches"]
        if match["status"] == "direct"
        for fact_id in match["fact_ids"]
    }
    uncovered_required = any(
        match["requirement_id"] in required_match_ids
        and match["status"] in {"gap", "unknown"}
        for match in result["matches"]
    )
    has_supported_match = any(
        match["status"] in {"direct", "transferable"}
        for match in result["matches"]
    )
    if result["role_family"] == "release_delivery":
        result["relevance"] = "low"
    elif not uncovered_required and len(direct_facts) >= 2:
        result["relevance"] = "high"
    elif has_supported_match:
        result["relevance"] = "medium"
    else:
        result["relevance"] = "low"

    required_items = [
        result["primary_goal"],
        *(item for item in result["items"] if item["kind"] in {"task", "must_have"}),
    ]
    fact_scores = {fact_id: 0 for fact_id in fact_ids}
    for item in required_items:
        weight = 4 if item["id"] == result["primary_goal"]["id"] else 2
        for fact in profile["facts"]:
            fact_scores[fact["id"]] += weight * len(
                set(item["capabilities"]) & set(fact["capabilities"])
            )
    for match in result["matches"]:
        bonus = 6 if match["status"] == "direct" else 3 if match["status"] == "transferable" else 0
        for fact_id in match["fact_ids"]:
            fact_scores[fact_id] += bonus

    required_capabilities = {
        capability for item in required_items for capability in item["capabilities"]
    }
    positionings = profile["positionings"]
    model_positioning = result["positioning_id"]
    result["positioning_id"] = sorted(
        positionings,
        key=lambda item: (
            -len(set(item["capabilities"]) & required_capabilities),
            0 if item["id"] == model_positioning else 1,
            item["id"],
        ),
    )[0]["id"]
    selected_positioning = next(
        item for item in profile["positionings"] if item["id"] == result["positioning_id"]
    )
    for fact in profile["facts"]:
        fact_scores[fact["id"]] += len(
            set(fact["domains"]) & set(selected_positioning["domains"])
        )
    is_portfolio_role = (
        "portfolio" in required_capabilities
        or "портфел" in result["role_summary"].casefold()
    )
    if is_portfolio_role:
        for fact in profile["facts"]:
            if set(fact["capabilities"]) & {"credit", "monetization"}:
                fact_scores[fact["id"]] += 5
    model_order = {fact_id: index for index, fact_id in enumerate(result["selected_fact_ids"])}
    ranked_fact_ids = sorted(
        fact_ids,
        key=lambda fact_id: (-fact_scores[fact_id], model_order.get(fact_id, 99), fact_id),
    )
    result["selected_fact_ids"] = ranked_fact_ids[:2]
    if is_portfolio_role:
        credit_fact = next(
            (fact_id for fact_id in ranked_fact_ids if "credit" in facts[fact_id]["capabilities"]),
            None,
        )
        monetization_fact = next(
            (
                fact_id
                for fact_id in ranked_fact_ids
                if fact_id != credit_fact
                and "monetization" in facts[fact_id]["capabilities"]
            ),
            None,
        )
        if credit_fact and monetization_fact:
            result["selected_fact_ids"] = [credit_fact, monetization_fact]
    return result


def _description_sentences(description: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", description)
        if sentence.strip()
    ]


def _description_fragments(description: str) -> list[str]:
    fragments = _description_sentences(description)
    lines = description.splitlines()
    for start in range(len(lines)):
        for width in range(2, 6):
            end = start + width
            if end > len(lines):
                break
            fragment = "\n".join(lines[start:end]).strip()
            if fragment and len(fragment) <= 900:
                fragments.append(fragment)
    return _deduplicate(fragments)


def _evidence_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zа-яё0-9+/.-]+", text.casefold())
        if len(token) > 2
    }


def _ground_item_evidence(item: dict, description: str) -> None:
    evidence = item.get("evidence") if isinstance(item, dict) else None
    text = item.get("text") if isinstance(item, dict) else None
    if not isinstance(evidence, str) or not isinstance(text, str):
        return
    if evidence.casefold() in description.casefold():
        return

    target = f"{text} {evidence}"
    target_tokens = _evidence_tokens(target)
    best_sentence = None
    best_score = 0.0
    for sentence in _description_fragments(description):
        sentence_tokens = _evidence_tokens(sentence)
        if not target_tokens or not sentence_tokens:
            continue
        coverage = len(target_tokens & sentence_tokens) / len(target_tokens)
        similarity = difflib.SequenceMatcher(
            None,
            target.casefold(),
            sentence.casefold(),
        ).ratio()
        score = max(coverage, similarity)
        if score > best_score:
            best_score = score
            best_sentence = sentence
    if best_sentence is not None and best_score >= 0.48:
        item["evidence"] = best_sentence


def _add_deterministic_items(result: dict, description: str) -> dict:
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        return result
    if isinstance(result.get("primary_goal"), dict):
        _ground_item_evidence(result["primary_goal"], description)
        inferred = _infer_capabilities(
            f"{result['primary_goal'].get('text', '')} {result['primary_goal'].get('evidence', '')}",
            default=False,
        )
        result["primary_goal"]["capabilities"] = _deduplicate(
            [*result["primary_goal"].get("capabilities", []), *inferred]
        )
    for model_item in result["items"]:
        _ground_item_evidence(model_item, description)
        if isinstance(model_item, dict):
            model_text = f"{model_item.get('text', '')} {model_item.get('evidence', '')}".casefold()
            inferred = _infer_capabilities(model_text, default=False)
            model_item["capabilities"] = _deduplicate(
                [*model_item.get("capabilities", []), *inferred]
            )
            if any(marker in model_text for marker in ("английск", "english")) and any(
                marker in model_text for marker in ("b1", "b2", "c1", "c2", "уров", "свобод")
            ):
                model_item["kind"] = "language"
                model_item["capabilities"] = []
                model_item["request_kind"] = "other"
    result["items"] = [
        item
        for item in result["items"]
        if not (
            isinstance(item, dict)
            and item.get("kind") in {"context", "nice_to_have", "geography"}
            and isinstance(item.get("evidence"), str)
            and item["evidence"].casefold() not in description.casefold()
        )
        and not (
            isinstance(item, dict)
            and item.get("kind") in {"task", "must_have"}
            and len(item.get("text", "")) > 280
        )
    ]
    existing_evidence = {
        item.get("evidence", "").casefold()
        for item in result["items"]
        if isinstance(item, dict)
    }
    capability_markers = {
        "ci_cd": ("ci/cd", "gitlab ci", "jenkins", "gitflow", "devops"),
        "agile": ("agile", "scrum"),
        "release_management": ("релиз", "release"),
        "crm": ("crm",),
        "risk": ("риск", "risk"),
        "retention": ("retention", "churn", "удержан"),
        "activation": ("activation", "активац"),
        "integrations": ("интеграц", "integration"),
        "hardware": ("pos-терминал", "платёжн", "платежн", "оборудован", "аппаратн"),
        "suppliers": ("поставщик", "supplier"),
    }
    must_markers = (
        "требован",
        "обязател",
        "необходим",
        "опыт ",
        "знание ",
        "понимание ",
        "владение ",
    )
    nice_markers = ("преимуществ", "будет плюсом", "желательно", "nice to have")
    task_markers = (
        "управлять ",
        "развивать ",
        "отвечать ",
        "работать ",
        "обеспечивать ",
        "контролировать ",
        "формировать ",
        "координировать ",
    )
    for sentence in _description_sentences(description):
        folded = sentence.casefold()
        item = None
        if "сопроводительн" in folded and any(
            marker in folded
            for marker in ("укаж", "указат", "напиш", "просьб", "добав")
        ):
            request_kind = (
                "compensation"
                if any(marker in folded for marker in ("доход", "зарплат", "оплат"))
                else "language"
                if any(marker in folded for marker in ("английск", "english"))
                else "portfolio"
                if "портфолио" in folded
                else "other"
            )
            item = {
                "id": "det_request_" + hashlib.sha256(sentence.encode()).hexdigest()[:10],
                "kind": "cover_letter_request",
                "text": sentence.rstrip(".!?"),
                "evidence": sentence,
                "capabilities": [],
                "request_kind": request_kind,
            }
            matching_items = [
                existing
                for existing in result["items"]
                if isinstance(existing, dict)
                and existing.get("evidence", "").casefold() == sentence.casefold()
            ]
            correct_item = next(
                (
                    existing
                    for existing in matching_items
                    if existing.get("kind") == "cover_letter_request"
                ),
                None,
            )
            if correct_item is not None:
                correct_item["request_kind"] = request_kind
                item = None
            else:
                result["items"] = [
                    existing for existing in result["items"] if existing not in matching_items
                ]
                existing_evidence.discard(sentence.casefold())
        elif any(marker in folded for marker in ("английск", "english")) and any(
            marker in folded for marker in ("b1", "b2", "c1", "c2", "уров", "свобод")
        ):
            item = {
                "id": "det_language_" + hashlib.sha256(sentence.encode()).hexdigest()[:10],
                "kind": "language",
                "text": sentence.rstrip(".!?"),
                "evidence": sentence,
                "capabilities": [],
                "request_kind": "other",
            }
        else:
            capabilities = [
                capability
                for capability, markers in capability_markers.items()
                if any(marker in folded for marker in markers)
            ]
            kind = None
            if capabilities and any(marker in folded for marker in nice_markers):
                kind = "nice_to_have"
            elif capabilities and any(marker in folded for marker in must_markers):
                kind = "must_have"
            elif capabilities and folded.startswith(task_markers):
                kind = "task"
            if kind:
                item = {
                    "id": "det_requirement_" + hashlib.sha256(sentence.encode()).hexdigest()[:10],
                    "kind": kind,
                    "text": sentence.rstrip(".!?"),
                    "evidence": sentence,
                    "capabilities": capabilities,
                    "request_kind": "other",
                }
        if item and sentence.casefold() not in existing_evidence:
            if len(result["items"]) >= 16:
                break
            result["items"].append(item)
            existing_evidence.add(sentence.casefold())
    if isinstance(result.get("matches"), list):
        valid_item_ids = {
            item.get("id")
            for item in [result.get("primary_goal", {}), *result["items"]]
            if isinstance(item, dict)
        }
        result["matches"] = [
            match
            for match in result["matches"]
            if isinstance(match, dict) and match.get("requirement_id") in valid_item_ids
        ]
    return result


def _infer_capabilities(text: str, default: bool = True) -> list[str]:
    folded = text.casefold()
    markers = {
        "portfolio": ("портфел", "portfolio"),
        "credit": ("кредит", "credit"),
        "monetization": ("доход", "монетизац", "profit", "revenue"),
        "growth": ("рост", "growth"),
        "retention": ("retention", "churn", "удержан"),
        "activation": ("activation", "активац"),
        "b2b": ("b2b", "корпоративн"),
        "fintech": ("финтех", "банк", "платёж", "платеж"),
        "launch": ("запуск", "с нуля", "новое направление"),
        "strategy": ("стратег", "roadmap", "приоритет"),
        "delivery": ("delivery", "разработ", "поставк", "релиз"),
        "team": ("команд", "лидер", "руковод"),
        "discovery": ("discovery", "исследован", "гипотез"),
        "ai": (" ai ", "llm", "искусственн", "нейросет"),
        "crm": ("crm",),
        "risk": ("риск", "risk"),
        "integrations": ("интеграц", "integration"),
        "release_management": ("релиз", "release"),
        "agile": ("agile", "scrum"),
        "ci_cd": ("ci/cd", "gitlab ci", "jenkins", "gitflow", "devops"),
        "hardware": ("pos-терминал", "платёжн", "платежн", "оборудован", "аппаратн"),
        "suppliers": ("поставщик", "supplier"),
    }
    capabilities = [
        capability
        for capability, values in markers.items()
        if any(value in f" {folded} " for value in values)
    ]
    if capabilities:
        return capabilities
    return ["strategy", "delivery"] if default else []


def _fallback_analysis(vacancy_title: str, vacancy_description: str, profile: dict) -> dict:
    sentences = _description_sentences(vacancy_description)
    if not sentences:
        raise OllamaUnavailableError("В описании вакансии нет текста для анализа")
    task_starts = (
        "управ",
        "развив",
        "отвеч",
        "обеспеч",
        "формир",
        "создав",
        "запуск",
    )

    def sentence_score(sentence: str) -> tuple[int, int]:
        folded = sentence.casefold().lstrip("•-— ")
        score = 0
        if "ключевая задач" in folded or "основная задач" in folded:
            score += 12
        if folded.startswith(task_starts):
            score += 8
        if any(marker in folded for marker in ("продукт", "стратег", "портфел", "рост", "развити")):
            score += 4
        if any(marker in folded for marker in ("о компании", "мы предлагаем", "условия", "требования")):
            score -= 6
        return score, -len(sentence)

    evidence = max(sentences, key=sentence_score)
    goal_text = evidence.strip().lstrip("•-— ").rstrip(".!?")
    if len(goal_text) > 240:
        goal_text = goal_text.split(";", 1)[0].strip()

    title_folded = vacancy_title.casefold()
    if any(marker in title_folded for marker in ("head of product", "cpo", "директор по продукт", "product director")):
        role_family = "product_leadership"
    elif "релиз" in evidence.casefold() or "release" in evidence.casefold():
        role_family = "release_delivery"
    else:
        role_family = "product"

    fallback = {
        "suitable": True,
        "role_family": role_family,
        "role_summary": "продуктовая роль; требуется ручная проверка деталей",
        "primary_goal": {
            "id": "fallback_goal",
            "kind": "task",
            "text": goal_text,
            "evidence": evidence,
            "capabilities": _infer_capabilities(f"{vacancy_title} {goal_text}"),
            "request_kind": "other",
        },
        "items": [],
        "matches": [],
        "relevance": "low",
        "positioning_id": profile["positionings"][0]["id"],
        "selected_fact_ids": [fact["id"] for fact in profile["facts"][:2]],
    }
    fallback = _add_deterministic_items(fallback, vacancy_description)
    fallback = _validate_analysis(fallback, vacancy_description, profile)
    fallback["fallback_used"] = True
    return fallback


async def analyze_vacancy(vacancy_title: str, vacancy_description: str) -> dict:
    profile = _load_candidate_profile()
    prompt = f"""
Ты анализируешь вакансию для подготовки честного сопроводительного письма.

Правила:
1. Отличай главную цель, ежедневные задачи, обязательные требования, преимущества и контекст компании.
2. Каждый пункт подкрепляй ДОСЛОВНЫМ непрерывным фрагментом description в evidence.
3. Не превращай контекст компании в требование кандидату.
4. Отдельно находи просьбы вида «укажите в сопроводительном письме».
5. request_kind используй compensation только для ожиданий по доходу; language — для просьбы указать язык; portfolio — для портфолио; иначе other.
6. Для пунктов не типа cover_letter_request ставь request_kind=other.
7. suitable=false только для явно исключённых ролей: продажи, Project Manager, Junior, чистая инженерия/аналитика/маркетинг без ответственности за продукт. Product в названии при release/delivery в содержании не отклоняй: relevance=low и role_family=release_delivery.
8. Сопоставляй требования только с фактами ниже. Не додумывай опыт.
9. direct — явно похожий опыт; transferable — близкая компетенция; gap — подтверждения нет; unknown — данных кандидата недостаточно.
10. high: обязательные требования закрыты и есть минимум два direct-факта. medium: есть direct, но есть переносимые навыки/пробелы. low: прямых доказательств нет или содержание роли отличается от названия.
11. Выбери ровно два наиболее сильных факта и одно позиционирование. Не пиши письмо.

ПРОФИЛЬ КАНДИДАТА:
{_profile_catalog(profile)}

ВАКАНСИЯ:
title: {vacancy_title}
description:
{vacancy_description}
"""
    last_error = None
    for attempt in range(2):
        retry_note = ""
        if last_error is not None:
            retry_note = (
                "\nПредыдущий ответ отклонён: "
                f"{last_error}. Исправь карточку; evidence копируй дословно."
            )
        try:
            answer = await _ask_ollama(
                prompt + retry_note,
                timeout_seconds=120,
                temperature=0.0,
                max_output_tokens=1800,
                response_format=_analysis_schema(profile),
            )
        except OllamaUnavailableError as error:
            if attempt == 0:
                last_error = error
                await asyncio.sleep(5)
                continue
            raise
        try:
            result = json.loads(answer)
            result = _add_deterministic_items(result, vacancy_description)
            return _validate_analysis(result, vacancy_description, profile)
        except (json.JSONDecodeError, TypeError) as error:
            last_error = OllamaUnavailableError("Ollama вернула неверный JSON анализа")
        except OllamaUnavailableError as error:
            last_error = error
    return _fallback_analysis(vacancy_title, vacancy_description, profile)


def _item_map(analysis: dict) -> dict[str, dict]:
    return {
        item["id"]: item
        for item in [analysis["primary_goal"], *analysis.get("items", [])]
    }


def build_warnings(analysis: dict, profile: dict) -> list[str]:
    items = _item_map(analysis)
    warnings = []
    for item in analysis.get("items", []):
        if item["kind"] != "cover_letter_request":
            continue
        request_kind = item["request_kind"]
        if request_kind == "compensation" and not profile["preferences"].get("compensation"):
            warnings.append(f"В письме требуется указать доход, но значение не задано: {item['text']}")
        elif request_kind == "language" and not profile["verified"].get("english"):
            warnings.append(f"В письме требуется указать язык, но данные не подтверждены: {item['text']}")
        elif request_kind in {"portfolio", "other"}:
            warnings.append(f"Отдельная просьба работодателя требует проверки: {item['text']}")

    if analysis.get("fallback_used"):
        warnings.append(
            "Модель не вернула корректную карточку: выполнен упрощённый анализ, проверьте вакансию вручную."
        )

    match_priority = {"language": 0, "must_have": 1, "task": 2}
    ordered_matches = sorted(
        analysis.get("matches", []),
        key=lambda match: match_priority.get(
            items.get(match["requirement_id"], {}).get("kind"), 9
        ),
    )
    for match in ordered_matches:
        item = items.get(match["requirement_id"])
        if item and match["status"] in {"gap", "unknown"} and item["kind"] in {
            "must_have",
            "language",
            "task",
        }:
            label = "Нет подтверждённого опыта" if match["status"] == "gap" else "Нужно уточнить опыт"
            warnings.append(f"{label}: {item['text']}")

    return _deduplicate(warnings)


def build_strengths(analysis: dict, profile: dict) -> list[str]:
    items = _item_map(analysis)
    facts = {fact["id"]: fact for fact in profile["facts"]}
    strengths = []
    for match in analysis.get("matches", []):
        if match["status"] not in {"direct", "transferable"}:
            continue
        item = items.get(match["requirement_id"])
        matched_facts = [facts[fact_id] for fact_id in match["fact_ids"] if fact_id in facts]
        if item and matched_facts:
            prefix = "Прямой опыт" if match["status"] == "direct" else "Переносимый опыт"
            strengths.append(f"{prefix}: {item['text']}")
        if len(strengths) == 3:
            break
    return strengths


def _sentence_fragment(text: str) -> str:
    cleaned = text.strip().rstrip(".!?;:")
    if not cleaned:
        return cleaned
    return cleaned[0].lower() + cleaned[1:]


def _compose_cover_letter(analysis: dict, profile: dict) -> str:
    positionings = {item["id"]: item for item in profile["positionings"]}
    facts = {fact["id"]: fact for fact in profile["facts"]}
    positioning = positionings[analysis["positioning_id"]]["text"]
    selected = [facts[fact_id] for fact_id in analysis["selected_fact_ids"]]
    goal = _sentence_fragment(analysis["primary_goal"]["text"])

    verified_english = profile["verified"].get("english")
    language_required = any(item["kind"] == "language" for item in analysis.get("items", []))
    english_text = ""
    if language_required and isinstance(verified_english, str) and verified_english.strip():
        english_text = f" Английский: {verified_english.strip().rstrip('.')} .".replace(" .", ".")

    compensation = profile["preferences"].get("compensation")
    compensation_required = any(
        item["kind"] == "cover_letter_request" and item["request_kind"] == "compensation"
        for item in analysis.get("items", [])
    )
    compensation_text = ""
    if compensation_required and isinstance(compensation, str) and compensation.strip():
        compensation_text = f" Мои зарплатные ожидания — {compensation.strip().rstrip('.')} .".replace(" .", ".")

    long_closing = (
        "Мне интересна возможность отвечать за этот результат и связывать "
        "продуктовые решения с измеримым эффектом для бизнеса и пользователей. "
        "Особенно привлекает ответственность за полный цикл решений и возможность "
        "оценивать их влияние по продуктовым и бизнес-показателям."
        f"{compensation_text} Буду рад подробно обсудить задачи роли, приоритеты "
        "и ожидаемые результаты."
    )
    short_closing = (
        "Мне интересна возможность отвечать за этот результат."
        f"{compensation_text} Буду рад подробно обсудить задачи роли, приоритеты "
        "и ожидаемые результаты."
    )
    paragraphs = [
        positioning,
        f"Ключевая задача этой роли — {goal}. {selected[0]['public_text']}",
        f"{selected[1]['public_text']}{english_text}",
        long_closing,
    ]

    def render() -> str:
        return "Здравствуйте!\n\n" + "\n\n".join(paragraphs) + f"\n\n{APPLICANT_NAME}"

    text = render()
    if len(re.findall(r"\b[\w-]+\b", text, flags=re.UNICODE)) > MAX_LETTER_WORDS:
        paragraphs[-1] = short_closing
        text = render()
    if len(re.findall(r"\b[\w-]+\b", text, flags=re.UNICODE)) < MIN_LETTER_WORDS:
        paragraphs[-1] += (
            " Готов предметно обсудить ограничения продукта и критерии успеха "
            "на ближайшем этапе."
        )
        text = render()
    return text


def _number_tokens(text: str) -> set[str]:
    return set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)?(?:\s?[%×xх])?", text.casefold()))


def _cover_letter_issues(
    text: str,
    profile: dict,
    approved_sources: str = "",
) -> list[str]:
    issues = []
    if not text.startswith("Здравствуйте!"):
        issues.append("письмо должно начинаться с «Здравствуйте!»")
    if not text.rstrip().endswith(APPLICANT_NAME):
        issues.append(f"последняя строка должна быть: {APPLICANT_NAME}")

    forbidden_terms = ["С уважением", *profile.get("forbidden_terms", []), *RISKY_PHRASES]
    for term in forbidden_terms:
        if term.casefold() in text.casefold():
            issues.append(f"запрещено упоминание: {term}")

    if re.search(r"\b[\w.+-]+@[\w.-]+\.\w+\b", text):
        issues.append("нельзя добавлять email")
    if re.search(r"(?:\+7|8\s?\(?\d{3}\)?)[\d\s()-]{7,}", text):
        issues.append("нельзя добавлять телефон")
    word_count = len(re.findall(r"\b[\w-]+\b", text, flags=re.UNICODE))
    if not MIN_LETTER_WORDS <= word_count <= MAX_LETTER_WORDS:
        issues.append(
            f"длина должна быть {MIN_LETTER_WORDS}–{MAX_LETTER_WORDS} слов, сейчас: {word_count}"
        )
    if len(text.split("\n\n")) != 6:
        issues.append("письмо должно содержать четыре смысловых блока")
    if approved_sources:
        unsupported_numbers = _number_tokens(text) - _number_tokens(approved_sources)
        if unsupported_numbers:
            issues.append(
                "найдены неподтверждённые числа: " + ", ".join(sorted(unsupported_numbers))
            )
    return issues


def _approved_sources(analysis: dict, profile: dict) -> str:
    positionings = {item["id"]: item for item in profile["positionings"]}
    facts = {fact["id"]: fact for fact in profile["facts"]}
    parts = [
        positionings[analysis["positioning_id"]]["text"],
        analysis["primary_goal"]["text"],
        *(facts[fact_id]["public_text"] for fact_id in analysis["selected_fact_ids"]),
    ]
    parts.extend(item["text"] for item in analysis.get("items", []))
    parts.extend(str(value) for value in profile["preferences"].values())
    parts.extend(str(value) for value in profile["verified"].values())
    return "\n".join(parts)


async def analyze_and_generate(vacancy_title: str, vacancy_description: str) -> dict:
    profile = _load_candidate_profile()
    analysis = await analyze_vacancy(vacancy_title, vacancy_description)
    letter = _compose_cover_letter(analysis, profile)
    issues = _cover_letter_issues(letter, profile, _approved_sources(analysis, profile))
    if issues:
        raise CandidateProfileError("; ".join(issues))
    return {
        "cover_letter": letter,
        "analysis": analysis,
        "warnings": build_warnings(analysis, profile),
        "strengths": build_strengths(analysis, profile),
        "letter_version": LETTER_VERSION,
    }


async def generate_cover_letter(vacancy_title: str, vacancy_description: str) -> str:
    result = await analyze_and_generate(vacancy_title, vacancy_description)
    return result["cover_letter"]


async def is_vacancy_suitable(vacancy_title: str, vacancy_description: str) -> bool:
    analysis = await analyze_vacancy(vacancy_title, vacancy_description)
    return analysis["suitable"]


def _raw_profile() -> dict:
    path = Path(CANDIDATE_PROFILE_PATH)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as error:
        raise CandidateProfileError(f"Не удалось прочитать профиль: {path}") from error


def _write_profile(profile: dict) -> None:
    path = Path(CANDIDATE_PROFILE_PATH)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        temp_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temp_path, 0o600)
        temp_path.replace(path)
        os.chmod(path, 0o600)
    except OSError as error:
        raise CandidateProfileError(f"Не удалось сохранить профиль: {path}") from error


async def draft_profile_fact(raw_text: str, requirement: str) -> dict:
    profile = _load_candidate_profile()
    prompt = f"""
Перепиши подтверждённый пользователем опыт в 1–2 спокойных предложения для сопроводительного письма.
Не добавляй работодателя, контакты, цифры, навыки или результаты, которых нет в исходном тексте.
Не используй фразы «на senior-уровне», «тёмные паттерны», «идеально подхожу».
Выбери только подтверждённые capabilities из закрытого списка.

Требование вакансии: {requirement}
Исходный текст пользователя: {raw_text}
"""
    schema = {
        "type": "object",
        "properties": {
            "public_text": {"type": "string"},
            "capabilities": {
                "type": "array",
                "items": {"type": "string", "enum": list(CAPABILITIES)},
                "minItems": 1,
                "uniqueItems": True,
            },
            "domains": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        },
        "required": ["public_text", "capabilities", "domains"],
    }
    answer = await _ask_ollama(
        prompt,
        timeout_seconds=45,
        temperature=0.0,
        max_output_tokens=320,
        response_format=schema,
    )
    try:
        draft = json.loads(answer)
    except (json.JSONDecodeError, TypeError) as error:
        raise OllamaUnavailableError("Ollama не смогла подготовить факт") from error
    if not isinstance(draft, dict) or set(draft) != {"public_text", "capabilities", "domains"}:
        raise OllamaUnavailableError("Ollama вернула неполный факт")
    if not isinstance(draft["public_text"], str) or not draft["public_text"].strip():
        raise OllamaUnavailableError("Ollama вернула пустой факт")
    if any(phrase in draft["public_text"].casefold() for phrase in RISKY_PHRASES):
        raise OllamaUnavailableError("Ollama использовала рискованную формулировку")
    if _number_tokens(draft["public_text"]) - _number_tokens(raw_text):
        raise OllamaUnavailableError("Ollama добавила неподтверждённые цифры")
    if any(term.casefold() in draft["public_text"].casefold() for term in profile["forbidden_terms"]):
        raise OllamaUnavailableError("Факт содержит запрещённое название")
    if not isinstance(draft["capabilities"], list) or not draft["capabilities"] or any(
        item not in CAPABILITIES for item in draft["capabilities"]
    ):
        raise OllamaUnavailableError("Ollama вернула неизвестную компетенцию")
    if not isinstance(draft["domains"], list) or any(not isinstance(item, str) for item in draft["domains"]):
        raise OllamaUnavailableError("Ollama вернула неверные домены")
    return draft


def save_profile_fact(draft: dict) -> str:
    profile = _raw_profile()
    text = draft["public_text"].strip()
    fact_id = "telegram_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    facts = profile.setdefault("facts", [])
    if any(fact.get("id") == fact_id for fact in facts if isinstance(fact, dict)):
        return fact_id
    facts.append(
        {
            "id": fact_id,
            "capabilities": draft["capabilities"],
            "domains": draft.get("domains", []),
            "public_text": text,
        }
    )
    profile["schema_version"] = 2
    _write_profile(profile)
    return fact_id


def save_profile_value(kind: str, value: str) -> None:
    profile = _raw_profile()
    cleaned = value.strip()
    if not cleaned:
        raise CandidateProfileError("Значение не может быть пустым")
    if kind == "compensation":
        profile.setdefault("preferences", {})["compensation"] = cleaned
    elif kind == "english":
        profile.setdefault("verified", {})["english"] = cleaned
    else:
        raise CandidateProfileError(f"Неизвестный тип данных: {kind}")
    profile["schema_version"] = 2
    _write_profile(profile)


def save_screening_answer(
    answer_id: str,
    question_terms: list[str],
    answer: str,
) -> None:
    cleaned_id = answer_id.strip()
    cleaned_terms = [term.strip() for term in question_terms if term.strip()]
    cleaned_answer = answer.strip()
    if not cleaned_id or not cleaned_terms or not cleaned_answer:
        raise CandidateProfileError("Ответ работодателю заполнен не полностью")

    profile = _raw_profile()
    items = profile.setdefault("screening_answers", [])
    replacement = {
        "id": cleaned_id,
        "question_terms": cleaned_terms,
        "answer": cleaned_answer,
    }
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("id") == cleaned_id:
            items[index] = replacement
            break
    else:
        items.append(replacement)
    profile["schema_version"] = 2
    _write_profile(profile)
