"""Publication fixtures start with a prepared main researcher, without forced children."""


def completed_sections(run):
    context = run.context_json
    run.context_json = {**context, "input_snapshot_cutoff": context.get("input_snapshot_cutoff") or context["cutoff"],
        "execution": {**context.get("execution", {}), "research_section": "overview"}}
