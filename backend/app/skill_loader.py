from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from .config import ROOT_DIR


SKILLS_ROOT = ROOT_DIR / "skills"


SKILL_CATALOG = [
    {
        "id": "steve-jobs-skill",
        "label": "Steve Jobs / 乔布斯",
        "path": SKILLS_ROOT / "steve-jobs-skill",
        "keywords": ["乔布斯", "jobs", "steve jobs", "steve", "史蒂夫"],
        "files": [
            "SKILL.md",
            "references/research/03-expression-dna.md",
            "references/research/05-decisions.md",
        ],
    },
    {
        "id": "musk-skill",
        "label": "Elon Musk / 马斯克",
        "path": SKILLS_ROOT / "musk-skill",
        "keywords": ["马斯克", "musk", "elon", "埃隆"],
        "files": [
            "SKILL.md",
            "references/zh/style-rules.md",
            "references/zh/worldview.md",
            "references/zh/routing.md",
        ],
    },
    {
        "id": "trump-skill-chinese",
        "label": "Donald Trump / 特朗普",
        "path": SKILLS_ROOT / "trump-skill-chinese",
        "keywords": ["特朗普", "川普", "trump", "donald"],
        "files": [
            "SKILL.md",
            "references/style-rules.md",
            "references/worldview.md",
        ],
    },
]


def _read_text(path: Path, limit: int = 3600) -> str:
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    return text[:limit]


@lru_cache
def loaded_skills() -> list[dict]:
    skills = []
    for item in SKILL_CATALOG:
        if not item["path"].exists():
            continue
        chunks = []
        for rel in item["files"]:
            content = _read_text(item["path"] / rel)
            if content:
                chunks.append(f"## {rel}\n{content}")
        if not chunks:
            continue
        skills.append(
            {
                "id": item["id"],
                "label": item["label"],
                "keywords": item["keywords"],
                "path": str(item["path"]),
                "instruction": "\n\n".join(chunks)[:9000],
            }
        )
    return skills


def match_skill(persona: dict) -> dict | None:
    target = " ".join(
        str(persona.get(key, "")) for key in ("name", "background", "tone")
    ).lower()
    for skill in loaded_skills():
        if any(keyword.lower() in target for keyword in skill["keywords"]):
            return skill
    return None


def skill_prompt_for_persona(persona: dict) -> str:
    skill = match_skill(persona)
    if not skill:
        return ""
    return (
        f"\n\n已匹配本地角色 skill：{skill['label']}。\n"
        "你必须优先遵循下面 skill 的人物语气、表达节奏、价值观与边界。"
        "不要逐字复述 skill 文件，只把它转化为角色发言风格。\n\n"
        f"{skill['instruction']}"
    )


def public_skill_summary(persona: dict) -> dict | None:
    skill = match_skill(persona)
    if not skill:
        return None
    return {"id": skill["id"], "label": skill["label"], "path": skill["path"]}
