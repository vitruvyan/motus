import asyncio, gc
from vitruvyan_motus import Runtime
from vitruvyan_motus.graph import GraphSpec
SPEC = GraphSpec.from_dict({
    "schema_version": "1.0.0", "name": "g", "version": "1", "entry": "n",
    "nodes": [{"name": "n", "effect_class": "pure"}],
    "transitions": {"n": {"kind": "terminal"}}})
ran = []
async def main():
    rt = Runtime(SPEC, {"n": lambda s: (ran.append(1), s)[1]})
    print("cancel() queued          :", rt.cancel("shutdown"))
    d = rt.astream(run_id="never-advanced")
    del d
    gc.collect()
    print("cancel() after abandon   :", rt.cancel("again"))
    r = await rt.arun(run_id="next")
    print("next run status          :", r.status, " nodes:", ran)
asyncio.run(main())
