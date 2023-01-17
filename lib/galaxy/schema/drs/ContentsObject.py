# generated by datamodel-codegen:
#   filename:  https://raw.githubusercontent.com/ga4gh/data-repository-service-schemas/master/openapi/components/schemas/ContentsObject.yaml
#   timestamp: 2022-05-21T22:19:13+00:00

from __future__ import annotations

from typing import (
    List,
    Optional,
)

from pydantic import (
    BaseModel,
    Field,
)


class Model(BaseModel):
    name: str = Field(
        ...,
        description="A name declared by the bundle author that must be used when materialising this object, overriding any name directly associated with the object itself. The name must be unique with the containing bundle. This string is made up of uppercase and lowercase letters, decimal digits, hyphen, period, and underscore [A-Za-z0-9.-_]. See http://pubs.opengroup.org/onlinepubs/9699919799/basedefs/V1_chap03.html#tag_03_282[portable filenames].",
    )
    id: Optional[str] = Field(
        None,
        description="A DRS identifier of a `DrsObject` (either a single blob or a nested bundle). If this ContentsObject is an object within a nested bundle, then the id is optional. Otherwise, the id is required.",
    )
    drs_uri: Optional[List[str]] = Field(
        None,
        description="A list of full DRS identifier URI paths that may be used to obtain the object. These URIs may be external to this DRS instance.",
        example="drs://drs.example.org/314159",
    )
    contents: Optional[List[Model]] = Field(
        None,
        description='If this ContentsObject describes a nested bundle and the caller specified "?expand=true" on the request, then this contents array must be present and describe the objects within the nested bundle.',
    )


Model.update_forward_refs()
