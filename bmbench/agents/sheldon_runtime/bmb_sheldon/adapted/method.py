"""The adapted method: SHELDON's own, with the changes in `adaptations` switched on.

`_loop` is SHELDON 7fbf027's `Sheldon.run`, copied verbatim. Each change enters it through
one marked block (`# >>> adaptation: <name>` to `# <<< adaptation: <name>`) that does
nothing unless the change is on. tests/test_sheldon_runtime.py takes the marked blocks
out and checks that what is left is the installed SHELDON's `Sheldon.run`, so an upgrade
cannot silently diverge from this copy. Every stage, prompt and state rule is SHELDON's.
"""

from __future__ import annotations

from sheldon.core.registry import methods
from sheldon.core.types import Prediction, RunContext, Task, Usage
from sheldon.methods.base import timer
from sheldon.methods.sheldon import candidate_analysis, resume, stages
from sheldon.methods.sheldon.loop import Sheldon, finalize, round_staged
from sheldon.methods.sheldon.run import Run

from bmb_sheldon.adapted import ADAPTATIONS, METHOD, early_stop, resolve


@methods.register(METHOD)
class AdaptedSheldon(Sheldon):
    name = METHOD
    blurb = "SHELDON with bmbench's adaptations: " + ", ".join(ADAPTATIONS)
    version = f"{Sheldon.version}+bmb"

    def adaptations(self) -> list[str]:
        return resolve(self.param("adaptations", []))

    def on(self, name: str) -> bool:
        return name in self.adaptations()

    async def run(self, task: Task, ctx: RunContext) -> Prediction:
        pred = await self._loop(task, ctx)
        checks = [{k: e.get(k) for k in ("round", "passed", "failed")} for e in ctx.trace.of_kind(early_stop.EVENT)]
        pred.extra = {**(pred.extra or {}), "adaptations": self.adaptations(), "early_stop": checks}
        return pred

    async def settled(self, run: Run) -> bool:
        return bool((await early_stop.check(run)).get("passed"))

    async def _loop(self, task: Task, ctx: RunContext) -> Prediction:
        mode = str(self.param("mode", "staged"))
        if mode not in ("staged", "session"):
            raise ValueError(f"sheldon: unknown mode {mode!r}; use staged or session")
        run = Run(ctx=ctx, task=task, param=self.param)
        usage = Usage()
        stop = "max_rounds"
        with timer() as t:
            # A resumed run opens on another run's belief state, so it must not
            # re-plan or re-measure the seed: both are already in the state it
            # just restored, and measuring the seed again spends an objective
            # call to learn a number it has.
            done = resume.apply(run)
            if not done:
                usage += await stages.plan(run)
                await candidate_analysis.measure_seed(run)
            cap = ctx.budget.max_rounds or int(self.param("rounds", 6))
            round_costs: list[float] = []
            for round_i in range(done + 1, done + cap + 1):
                why = ctx.budget.reason()
                if why is not None:
                    stop = f"budget:{why.split(':')[0]}"
                    break
                # A spent objective budget leaves
                # a round nothing to measure; opening one pays for refusals.
                exhausted = getattr(ctx.benchmark, "objective_exhausted", None)
                if exhausted is not None and exhausted(task):
                    stop = "objective_exhausted"
                    break
                # A round is not opened that the money left cannot mostly pay
                # for: every loop in such a round is cut off, and an engine
                # costed after the fact takes the run past its cap.
                left = ctx.budget.usd_left()
                if round_costs and left is not None:
                    per_round = sum(round_costs) / len(round_costs)
                    if left < float(self.param("round_reserve", 0.5)) * per_round:
                        stop = "budget:round_reserve"
                        ctx.trace.log("budget.stop", where="round", usd_left=left, usd_per_round=per_round)
                        break
                opened_at = ctx.budget.spent.cost_usd
                ctx.budget.start_round()
                run.round = round_i
                if mode == "session":
                    from sheldon.methods.sheldon import session  # noqa: PLC0415

                    u, tested = await session.round_session(run)
                else:
                    u, tested = await round_staged(run)
                usage += u
                round_costs.append(ctx.budget.spent.cost_usd - opened_at)
                run.state.close_round()
                ctx.trace.log("round.done", round=round_i, best=run.state.best(),
                              tested=[h.id for h in run.state.of("tested")],
                              shelved=[h.id for h in run.state.of("shelved")],
                              observations=len(run.state.observations))
                if not tested:
                    stop = "nothing_to_test"
                    break
                if not ctx.budget.ok():
                    stop = f"budget:{ctx.budget.spend_reason()}"
                    break
                # >>> adaptation: early_stop
                # Only when another round would follow; finalize below is unchanged.
                if self.on(early_stop.NAME) and round_i < done + cap and await self.settled(run):
                    stop = early_stop.STOP_REASON
                    break
                # <<< adaptation: early_stop
            attempts, u = await finalize(run)
            usage += u
        snapshot = {"round": run.round, "stop_reason": stop, **run.state.snapshot()}
        ctx.trace.log("sheldon.final", **snapshot)
        return Prediction(
            task_id=task.id, answer=attempts[0] if attempts else None, attempts=attempts,
            belief=snapshot, rounds=run.round, usage=usage, wall_s=t.elapsed,
            extra={"stop_reason": stop},
        )


def loop_without_adaptations(source: str) -> str:
    """`_loop`'s source with every marked block taken out and its name set back to `run`:
    what must equal SHELDON's `Sheldon.run`."""
    out, skipping = [], False
    for line in source.splitlines():
        marker = line.strip()
        if marker.startswith("# >>> adaptation:"):
            skipping = True
            continue
        if marker.startswith("# <<< adaptation:"):
            skipping = False
            continue
        if not skipping:
            out.append(line)
    text = "\n".join(out) + ("\n" if source.endswith("\n") else "")
    return text.replace("async def _loop(", "async def run(", 1)
