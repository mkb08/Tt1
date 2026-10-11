"""Claude writes and fact-checks scripts through the Anthropic API (needs ANTHROPIC_API_KEY).

No API key? Use the Claude Code GitHub Action with a Pro/Max subscription instead:
it follows the same rules from CLAUDE.md and writes the script file directly.
"""

import datetime as dt
import json
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from .config import QUEUE
from .schema import WORDS_PER_SECOND, Script, check_script, save_script, slugify

# Server-side fallback: if a safety classifier declines a request (rare for health
# education, but possible), the API retries it on Anthropic's recommended fallback model.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class WriterError(RuntimeError):
    pass


class FactCheck(BaseModel):
    verdict: Literal["pass", "fail"]
    issues: list[str] = Field(description="One entry per problem: scene number, the exact phrase, what is wrong, and a corrected wording")


def _format_brief(cfg: dict) -> str:
    target = cfg["format"]["target_seconds"]
    if cfg["format"]["type"] == "short":
        return (f"a vertical YouTube Short of about {target} seconds (~{int(target * WORDS_PER_SECOND)} spoken words), "
                f"6-10 scenes of 1-2 sentences each")
    return (f"a horizontal long-form video of about {target // 60} minutes (~{int(target * WORDS_PER_SECOND)} spoken words), "
            f"organised into scenes of 2-4 sentences, with a clear structure and a payoff at the end")


def system_prompt(cfg: dict) -> str:
    ch = cfg["channel"]
    rules = "\n".join(f"- {r}" for r in cfg["writer"]["rules"])
    return f"""You write scripts for "{ch['name']}", a faceless YouTube channel about {ch['niche']}, for {ch['audience']}.

Each script becomes {_format_brief(cfg)}. A neural text-to-speech voice reads the narration over stock footage, with word-by-word captions on screen, so write for the ear.

What makes these videos get watched:
- The first sentence is the hook. Viewers decide within about two seconds, so open with the most surprising accurate finding or a question the video answers. No greetings, no "in this video".
- Plain spoken language, short sentences, roughly a 7th-grade reading level. Write numbers the way they are spoken ("about one in three", "twenty minutes").
- One idea per scene. Every scene should give the viewer a reason to keep watching the next one.
- visual_query is a concrete, filmable stock-footage search (people doing things, objects, places). Never charts, text, logos or real named people.
- The title is under 70 characters, specific and curiosity-driven, and the video fully delivers what it promises. No clickbait that the content does not back up.

Accuracy rules. These override everything above:
{rules}
- Every factual claim must come from the evidence items in the research file and be cited in that scene's source_ids, using the ids exactly as given. Never invent studies, numbers, quotes or ids. If the evidence is weak or mixed, say so plainly.
- In sources, copy each cited item's title exactly, and describe the study type, population and size in note."""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _call(cfg: dict, system: str, user: str, output_format):
    response = _client().beta.messages.parse(
        model=cfg["writer"]["model"],
        max_tokens=16000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": cfg["writer"].get("effort", "high")},
        output_format=output_format,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        raise WriterError(f"Claude declined the request (category: {category}). Try a different topic.")
    if response.stop_reason == "max_tokens":
        raise WriterError("Claude ran out of output tokens; shorten target_seconds or the research input.")
    if response.parsed_output is None:
        raise WriterError(f"No structured output returned (stop_reason={response.stop_reason}).")
    return response.parsed_output


def draft(cfg: dict, research: dict, feedback: list[str] | None = None) -> Script:
    payload = {k: research[k] for k in ("requested_topic", "recent_topics", "insights", "evidence", "signals")}
    user = (
        "Research file (JSON):\n<research>\n" + json.dumps(payload, ensure_ascii=False) + "\n</research>\n\n"
        "Choose the single best topic for the next video: the strongest, most useful evidence with the most "
        "surprising accurate hook, that is not a repeat of recent_topics. If requested_topic is set, make the video "
        "about it. Use the insights (what has performed well on this channel) and the trend signals when choosing "
        "the angle and title. Then write the script."
    )
    if feedback:
        user += ("\n\nA fact-checker rejected your previous draft for these reasons. Write a corrected script that "
                 "fixes every point:\n" + "\n".join(f"- {f}" for f in feedback))
    return _call(cfg, system_prompt(cfg), user, Script)


def fact_check(cfg: dict, script: Script, research: dict) -> FactCheck:
    cited = {s.id for s in script.sources}
    evidence = [e for e in research["evidence"] if e["id"] in cited]
    rules = "\n".join(f"- {r}" for r in cfg["writer"]["rules"])
    system = f"""You are a meticulous fact-checker reviewing a script for a YouTube channel about {cfg['channel']['niche']} before it is published. You have the script and the full text or abstract of every source it cites.

Check every factual statement in the narration, title, thumbnail text and description:
- Is it supported by the cited source itself, not merely plausible? Uncited statements are acceptable only if they are uncontroversial common knowledge.
- Does it keep the source's strength intact: study type, population, effect size, association versus causation, uncertainty?
- Does the title or thumbnail promise more than the evidence shows?
- Does it break any of the channel's rules:
{rules}

Return "fail" if anything would mislead a viewer or breaks a rule. Wording preferences alone are a "pass"."""
    user = ("<script>\n" + script.model_dump_json(indent=2) + "\n</script>\n\n<sources>\n"
            + json.dumps(evidence, indent=2, ensure_ascii=False) + "\n</sources>")
    return _call(cfg, system, user, FactCheck)


def write(cfg: dict, research: dict, research_ids: set[str] | None) -> str:
    """Draft, validate and fact-check a script, then queue it. Returns the queued path."""
    feedback: list[str] | None = None
    for attempt in (1, 2):
        print(f"Writer: drafting with {cfg['writer']['model']} (attempt {attempt})")
        script = draft(cfg, research, feedback)
        errors, warnings = check_script(script, cfg, research_ids)
        for w in warnings:
            print(f"  warning: {w}")
        if errors:
            feedback = errors
            print("  validation failed: " + "; ".join(errors))
            continue
        if not cfg["writer"].get("fact_check", True):
            break
        print("Writer: fact-checking")
        check = fact_check(cfg, script, research)
        if check.verdict == "pass":
            break
        feedback = check.issues
        print("  fact-check failed:\n  - " + "\n  - ".join(check.issues))
    else:
        raise WriterError("Script failed validation or fact-check twice:\n- " + "\n- ".join(feedback or []))

    path = QUEUE / f"{dt.date.today().isoformat()}-{slugify(script.topic)}.json"
    save_script(script, path)
    print(f"Writer: queued {path}")
    return str(path)


def insights(cfg: dict, performance: dict) -> str:
    """Turn channel stats into short guidance the writer reads before every script."""
    response = _client().beta.messages.create(
        model=cfg["writer"]["model"],
        max_tokens=8000,
        betas=[FALLBACK_BETA],
        fallbacks="default",
        output_config={"effort": "medium"},
        system=(f"You are the growth strategist for a faceless YouTube channel about {cfg['channel']['niche']}. "
                "You read the channel's performance data and write brief, specific guidance for the scriptwriter."),
        messages=[{"role": "user", "content": (
            "<performance>\n" + json.dumps(performance, indent=2, ensure_ascii=False) + "\n</performance>\n\n"
            "Write markdown under 300 words: what is working (topics, hooks, title patterns), what is not, and five "
            "concrete recommendations for the next videos. Base every point on the numbers; if there is too little data "
            "to tell, say so and suggest what to test.")}],
    )
    if response.stop_reason == "refusal":
        raise WriterError("Claude declined the insights request.")
    return "".join(b.text for b in response.content if b.type == "text").strip() + "\n"
