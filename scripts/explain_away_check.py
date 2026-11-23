"""Blind explain-away test: give the model the footprint predicate's top-10 families (v1,
pre-registered) and nothing else; it attributes each family's source and names the drug.
Usage: .venv/bin/python scripts/explain_away_check.py <fp_v1.txt> <out.jsonl> <n_samples>"""
import asyncio, json, re, sys
from pathlib import Path
sys.path.insert(0, ".")
from bmbench.config import load_dotenv
from bmbench.grader import judge_endpoint, _post

QUESTION = ("What parent drug was administered to this patient given the untargeted mass spectrometry data? "
            "Provide the answer as its generic drug name.")
SYSTEM = ("You analyse untargeted LC-MS/MS data. Attribute signals to their sources before naming anything: "
          "a patient sample also contains endogenous metabolites, dietary compounds, and sample-handling or "
          "instrument contaminants.")
SOURCES = ["endogenous", "diet", "matrix_or_contaminant", "administered_drug_or_metabolites", "unclear"]
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["families", "parent_drug", "drug_family_rank"],
          "properties": {
              "families": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                           "required": ["rank", "source", "identity", "reasoning"],
                           "properties": {"rank": {"type": "integer"}, "source": {"type": "string", "enum": SOURCES},
                                          "identity": {"type": "string"}, "reasoning": {"type": "string"}}}},
              "parent_drug": {"type": "string"},
              "drug_family_rank": {"type": "integer"}}}


def families(text: str, k: int = 10) -> str:
    blocks = re.split(r"\n(?=#\d+ family)", text)
    keep = [b.strip() for b in blocks if b.startswith("#")][:k]
    return "\n\n".join(keep)


def prompt(fams: str) -> str:
    return (f"**Question**\n\n{QUESTION}\n\n**How the families below were found**\n\n"
            "Positive-mode ESI, Orbitrap MS2 (HCD 30). Every MS2 precursor was merged within 5 ppm into a "
            "feature. Two features are linked when their mass difference matches a common phase I/II "
            "biotransformation (listed on each edge) AND they share at least one fragment ion (m/z >= 100). "
            "A family is a connected group of linked features, ranked by size. Intensity was not used for "
            "ranking. 'intensity rank' is the feature's precursor intensity rank among all 590 features.\n\n"
            f"**Families (top 10)**\n\n{fams}\n\n**Task**\n\nFor each family, attribute its most likely "
            "source with a short reason grounded in the masses, mass differences and shared fragments. Then "
            "name the parent drug administered (generic name) and give the rank of the family it comes from.")


async def main(src, out, n):
    load_dotenv()
    url, key = judge_endpoint()
    body = {"model": "gpt-6-luna", "reasoning_effort": "high",
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt(families(Path(src).read_text()))}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "attribution", "strict": True, "schema": SCHEMA}}}
    Path(out).with_suffix(".prompt.txt").write_text(body["messages"][1]["content"])
    async def one(i):
        data, _ = await asyncio.to_thread(_post, url, key, body, 600)
        return json.loads(data["choices"][0]["message"]["content"]), data.get("usage")
    res = await asyncio.gather(*(one(i) for i in range(n)), return_exceptions=True)
    with open(out, "w") as f:
        for r in res:
            if isinstance(r, Exception):
                print("ERROR", type(r).__name__, str(r)[:300]); continue
            v, usage = r
            f.write(json.dumps({"verdict": v, "usage": usage}) + "\n")
            print(f"parent_drug={v['parent_drug']!r} from family #{v['drug_family_rank']}; sources: "
                  + ", ".join(f"#{x['rank']}:{x['source']}" for x in v["families"]))

asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
