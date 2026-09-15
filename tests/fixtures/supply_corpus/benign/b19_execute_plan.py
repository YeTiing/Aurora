def execute_plan(plan: list[str]) -> list[str]:
    # Business terminology, not Python exec/eval.
    return [step.strip() for step in plan]
