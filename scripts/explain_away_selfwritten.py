"""Explain-away check 3: does the model write the answer footprint itself? (hb048)

Pre-registered before the run:
  input      features.json, built here with answer-neutral preprocessing only: MS2 precursors
             merged within 5 ppm; per feature its consensus fragments (>= 2% of base peak,
             m/z >= 40, in >= 50% of its scans, top 30). No biotransformation list, no fragment
             filtering, no family building. Those are what the model must come up with.
  write      gpt-6-luna gets the question, a description of features.json and 3 example rows.
             The system prompt is data-type neutral: state the answer's footprint as relations
             among data units, state the background and how the footprint separates it, write a
             stdlib Python program ranking groups by how fully they satisfy the footprint, not by
             magnitude, and never search for a named answer. One repair call if the program fails.
  run        docker, --network none, 3 GB, 300 s, features.json read-only; stdout cut at 8,000 chars.
  answer     a fresh call gets the question, the footprint statement, the code and its output;
             it attributes each reported group to a source, then names the drug.
  pass       diphenhydramine named in >= 3 of 5 samples (official judge not needed: exact
             generic name). Secondary: the 256.17 parent appears in the program's output.
Usage: .venv/bin/python scripts/explain_away_selfwritten.py <mzML> <out_dir> <n_samples>
"""
import asyncio, json, re, subprocess, sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from bmbench.config import load_dotenv
from bmbench.grader import judge_endpoint, _post
from footprint_ms_check import spectra

MODEL, IMAGE, PPM = "gpt-6-luna", "bmbench-sandbox:latest", 5e-6
QUESTION = ("What parent drug was administered to this patient given the untargeted mass spectrometry data? "
            "Provide the answer as its generic drug name.")

WRITE_SYSTEM = """You design an analysis that finds the answer to a question in a dataset. Work in this order.
1. Footprint. Whatever the answer turns out to be, what would it leave in this data? State it as measurable relations among units of the data (values, differences, shared sub-signals, co-occurrence, group contrasts), not as a specific named answer.
2. Background. What else in such a sample produces strong or structured signals that are not the answer? Say how the footprint tells the answer apart from it.
3. Program. Write a Python 3 program, standard library only, that computes the footprint over the whole table and prints the groups of units that satisfy it, ranked by how fully they satisfy it, not by signal magnitude, with the evidence for each (the units, the relations found, the values). Print at most 10 groups and at most 6,000 characters. Do not name, hard-code or search for any specific candidate answer.
The program is run as: python3 footprint.py /data/features.json"""

WRITE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["footprint", "background", "code"],
                "properties": {"footprint": {"type": "string"}, "background": {"type": "string"}, "code": {"type": "string"}}}

SOURCES = ["endogenous", "diet", "matrix_or_contaminant", "administered_drug_or_metabolites", "unclear"]
ANSWER_SYSTEM = ("You analyse untargeted LC-MS/MS data. Attribute signals to their sources before naming anything: "
                 "a patient sample also contains endogenous metabolites, dietary compounds, and sample-handling or "
                 "instrument contaminants.")
ANSWER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["groups", "parent_drug", "drug_group"],
                 "properties": {
                     "groups": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                "required": ["group", "source", "identity", "reasoning"],
                                "properties": {"group": {"type": "string"}, "source": {"type": "string", "enum": SOURCES},
                                               "identity": {"type": "string"}, "reasoning": {"type": "string"}}}},
                     "parent_drug": {"type": "string"}, "drug_group": {"type": "string"}}}


def build_features(path):
    feats = []
    for s in sorted(spectra(path), key=lambda s: s["prec"]):
        if feats and abs(s["prec"] - feats[-1]["s"][-1]["prec"]) <= PPM * s["prec"]:
            feats[-1]["s"].append(s)
        else:
            feats.append({"s": [s]})
    out = []
    for k, f in enumerate(feats, 1):
        sc = f["s"]
        acc = defaultdict(list)
        for s in sc:
            if not s["it"]:
                continue
            base = max(s["it"])
            seen = {}
            for m, i in zip(s["mz"], s["it"]):
                b = round(m / 0.003)
                if i >= 0.02 * base and m >= 40 and i > seen.get(b, (0, 0))[1]:
                    seen[b] = (m, i)
            for b, (m, i) in seen.items():
                acc[b].append((m, 100 * i / base))
        frags = [(sum(m for m, _ in v) / len(v), sum(r for _, r in v) / len(v))
                 for v in acc.values() if len(v) >= 0.5 * len(sc)]
        frags = sorted(sorted(frags, key=lambda x: -x[1])[:30])
        out.append({"id": f"F{k}", "mz": round(sum(s["prec"] for s in sc) / len(sc), 5),
                    "rt": [round(min(s["rt"] for s in sc), 2), round(max(s["rt"] for s in sc), 2)],
                    "n_ms2": len(sc), "max_precursor_intensity": round(max(s["pint"] for s in sc)),
                    "fragments": [[round(m, 4), round(r, 1)] for m, r in frags]})
    return out


def describe(feats):
    ex = "\n".join(json.dumps(f) for f in (feats[100], feats[300], feats[500]))
    return (f"**Question**\n\n{QUESTION}\n\n**Data**\n\nOne untargeted LC-MS/MS run of a patient sample: positive-mode "
            "ESI, Orbitrap, data-dependent MS2 (HCD). /data/features.json is a JSON list of "
            f"{len(feats)} precursor features. Each has: id; mz, the precursor m/z (scans merged within 5 ppm); "
            "rt, [first, last] scan start time; n_ms2, the number of MS2 scans; max_precursor_intensity; "
            "fragments, consensus product ions as [m/z, mean intensity in % of the scan's base peak] (ions >= 2% "
            "of base peak, m/z >= 40, present in at least half of the feature's MS2 scans, at most 30).\n\n"
            f"Three example rows:\n{ex}")


def run_code(code, work):
    (work / "footprint.py").write_text(code)
    try:
        p = subprocess.run(["docker", "run", "--rm", "--network", "none", "--memory", "3g",
                            "-v", f"{work}:/data:ro", IMAGE, "python3", "/data/footprint.py", "/data/features.json"],
                           capture_output=True, text=True, timeout=300)
        return p.returncode, p.stdout[:8000], p.stderr[-3000:]
    except subprocess.TimeoutExpired:
        return -1, "", "timeout after 300 s"


async def ask(url, key, system, user, schema, name, history=()):
    body = {"model": MODEL, "reasoning_effort": "high",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}, *history],
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}}
    data, cost = await asyncio.to_thread(_post, url, key, body, 900)
    return json.loads(data["choices"][0]["message"]["content"]), data["choices"][0]["message"]["content"], data.get("usage"), cost


async def sample(i, url, key, desc, out):
    work = out / f"s{i}"
    work.mkdir(exist_ok=True)
    (work / "features.json").write_text((out / "features.json").read_text())
    rec = {"sample": i, "usage": []}
    w, raw, u, c = await ask(url, key, WRITE_SYSTEM, desc, WRITE_SCHEMA, "footprint")
    rec["usage"].append([u, c])
    rc, so, se = await asyncio.to_thread(run_code, w["code"], work)
    if rc != 0 or not so.strip():
        rec["first_error"] = se
        fix = (f"The program failed (exit {rc}).\nstderr:\n{se}\nstdout:\n{so[:2000]}\n"
               "Return the corrected footprint, background and code.")
        w, _, u, c = await ask(url, key, WRITE_SYSTEM, desc, WRITE_SCHEMA, "footprint",
                               [{"role": "assistant", "content": raw}, {"role": "user", "content": fix}])
        rec["usage"].append([u, c])
        rc, so, se = await asyncio.to_thread(run_code, w["code"], work)
    rec.update(write=w, returncode=rc, stdout=so, stderr=se)
    (work / "features.json").unlink()
    if rc != 0 or not so.strip():
        rec["answer"] = None
        return rec
    user = (f"**Question**\n\n{QUESTION}\n\n**Analysis**\n\nA footprint program was run over all {desc.split('JSON list of ')[1].split()[0]} "
            f"precursor features of one positive-mode LC-MS/MS run.\n\nFootprint it tests:\n{w['footprint']}\n\n"
            f"Background it expects:\n{w['background']}\n\nCode:\n```python\n{w['code']}\n```\n\n**Output**\n\n```\n{so}\n```\n\n"
            "**Task**\n\nFor each group in the output, attribute its most likely source with a short reason grounded "
            "in the masses, mass differences and fragments. Then name the parent drug administered (generic name) "
            "and the group it comes from.")
    a, _, u, c = await ask(url, key, ANSWER_SYSTEM, user, ANSWER_SCHEMA, "attribution")
    rec["usage"].append([u, c])
    rec["answer"] = a
    return rec


async def main(mzml, out, n):
    load_dotenv()
    url, key = judge_endpoint()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    fpath = out / "features.json"
    if not fpath.exists():
        fpath.write_text(json.dumps(build_features(mzml)))
    feats = json.loads(fpath.read_text())
    desc = describe(feats)
    (out / "write.prompt.txt").write_text(WRITE_SYSTEM + "\n\n---\n\n" + desc)
    res = await asyncio.gather(*(sample(i, url, key, desc, out) for i in range(1, n + 1)), return_exceptions=True)
    with open(out / "samples.jsonl", "w") as f:
        for r in res:
            if isinstance(r, Exception):
                print("ERROR", type(r).__name__, str(r)[:300]); continue
            f.write(json.dumps(r) + "\n")
            a = r["answer"]
            hit256 = bool(re.search(r"256\.1[67]", r["stdout"]))
            print(f"s{r['sample']}: rc={r['returncode']} repaired={'first_error' in r} 256.17_in_output={hit256} "
                  f"answer={a and a['parent_drug']!r} from {a and a['drug_group']!r}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3])))
