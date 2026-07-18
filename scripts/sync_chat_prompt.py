"""Copy prompts/chat_assistant_system.md into the chat workflow's AI Agent node.

The §6.5 system prompt is version-controlled in `prompts/` (CLAUDE.md guardrail:
prompts live in prompts/, not hardcoded in workflows), but an n8n AI Agent node
cannot read a repo file — it needs the text inline. That forces one copy, and a
copy is only safe if it is mechanical and checked:

  * this script is the only writer of the embedded copy, and
  * `eval.run.check_chat_prompt_drift()` fails the eval summary if they diverge.

Usage:  python scripts/sync_chat_prompt.py [--check]
        --check exits non-zero on drift instead of rewriting (for CI).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT = REPO_ROOT / "prompts" / "chat_assistant_system.md"
WORKFLOW = REPO_ROOT / "n8n" / "chat_assistant.workflow.json"


def canonical_prompt() -> str:
    """The prompt file minus its leading maintainer comment block."""
    return re.sub(r"^<!--.*?-->\s*", "", PROMPT.read_text(encoding="utf-8"), flags=re.S).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="report drift, do not rewrite")
    args = parser.parse_args()

    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    agents = [n for n in wf["nodes"] if n.get("type", "").endswith("langchain.agent")]
    if len(agents) != 1:
        print(f"expected exactly one AI Agent node, found {len(agents)}", file=sys.stderr)
        return 2

    want = canonical_prompt()
    node = agents[0]
    have = node.get("parameters", {}).get("options", {}).get("systemMessage")

    if have == want:
        print("in sync")
        return 0
    if args.check:
        print(
            f"DRIFT: {WORKFLOW.name} systemMessage differs from {PROMPT.name}.\n"
            "Run: python scripts/sync_chat_prompt.py",
            file=sys.stderr,
        )
        return 1

    node.setdefault("parameters", {}).setdefault("options", {})["systemMessage"] = want
    WORKFLOW.write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"synced {len(want)} chars into {WORKFLOW.name} (re-import it for n8n to pick it up)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
