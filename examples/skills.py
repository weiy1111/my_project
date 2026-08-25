from pydantic import BaseModel

from auto_agent.skills import PythonSkill


class AddNumbersInput(BaseModel):
    left: int
    right: int


class AddNumbersOutput(BaseModel):
    total: int


def add_numbers_skill() -> PythonSkill:
    async def handler(arguments: AddNumbersInput, _context) -> AddNumbersOutput:
        return AddNumbersOutput(total=arguments.left + arguments.right)

    return PythonSkill(
        name="add_numbers",
        description="计算两个整数的和。",
        input_model=AddNumbersInput,
        output_model=AddNumbersOutput,
        handler=handler,
    )
