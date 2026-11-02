from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class InvitationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: EmailStr
    profile_id: int = Field(gt=0)
    team_ids: list[int] = Field(default_factory=list)


class InvitationAccept(BaseModel):
    token: str = Field(min_length=20, max_length=2048)
    password: str = Field(min_length=12, max_length=72)

    @field_validator("password")
    @classmethod
    def password_fits_bcrypt(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 UTF-8 bytes")
        return value


class UserActivationUpdate(BaseModel):
    is_active: bool


class InvitationCreated(BaseModel):
    id: int
    email: str
    is_active: bool


class InvitationAccepted(BaseModel):
    email: str


class InvitationProfile(BaseModel):
    id: int
    name: str
    interface: str


class InvitationTeam(BaseModel):
    id: int
    name: str


class InvitationOptions(BaseModel):
    profiles: list[InvitationProfile]
    teams: list[InvitationTeam]


class ProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    interface: str
    rights: dict


class ManagedUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    name: str
    entity_id: int
    is_active: bool
    profile: ProfileRead


class OwnEntity(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    name: str
    entity_id: int
    is_active: bool
    profile: ProfileRead


class MeRead(UserRead):
    entity: OwnEntity | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20)


class EntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    parent_id: int | None
