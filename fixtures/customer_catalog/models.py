from pydantic import BaseModel, Field, root_validator, validator


class Customer(BaseModel):
    name: str
    external_id: int = Field(alias="id")

    class Config:
        allow_population_by_field_name = True

    @validator("name")
    def normalize(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("name cannot be empty")
        return value


class QuantityBand(BaseModel):
    minimum: int
    maximum: int

    @root_validator(skip_on_failure=True)
    def validate_bounds(cls, values):
        if values["minimum"] > values["maximum"]:
            raise ValueError("minimum cannot exceed maximum")
        return values


def read_band(raw):
    return QuantityBand.parse_obj(raw)


def summarize_customer(name, identifier):
    return Customer(name=name, id=identifier).dict(by_alias=True)
