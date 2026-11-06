"""bmbench's runtime for SHELDON, installed next to SHELDON inside the sandbox image.

Nothing here edits SHELDON. Three pieces plug into it through its own extension points:

  benchmark.py   BioMysteryBench as a SHELDON benchmark (its registry and `Benchmark` base)
  transport.py   an OpenAI Responses API backend (SHELDON's `LLM`, passed as `llm=`)
  driver.py      the command the sandbox runs: one problem, the time limit, the result line
"""

__version__ = "0.1.0"

#: The SHELDON commit this runtime is written against and the image installs.
SHELDON_COMMIT = "7fbf027d1fb414443612d4ad2661b396c8352cd3"
