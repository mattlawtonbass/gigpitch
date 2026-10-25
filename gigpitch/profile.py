"""The band profile: every band-specific fact the bot uses lives here and nowhere else."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Sender(BaseModel):
    name: str
    role: str = "band leader"
    email: str
    phone: str = ""
    signature: str
    example_subject: str = ""


class Fees(BaseModel):
    weekend_guide: str = ""
    weekday_guide: str = ""
    notes: str = ""


class Targeting(BaseModel):
    region: str
    venue_types: list[str]
    avoid: list[str] = Field(default_factory=list)


class Links(BaseModel):
    epk: str = ""
    website: str = ""
    video: str = ""
    other: dict[str, str] = Field(default_factory=dict)


class BandProfile(BaseModel):
    slug: str
    name: str
    pitch: str
    bio: str
    line_up: list[str]
    formats: str = ""
    sender: Sender
    fees: Fees = Fees()
    targeting: Targeting
    do_not_contact: list[str] = Field(default_factory=list)
    links: Links = Links()
    current_focus: str = ""
    availability: str = ""
    style: str = ""
    leads_per_week: int = 5

    def as_brief(self) -> str:
        """The profile as plain text for the model. Fees are included as guides only."""
        lines = [
            f"Band: {self.name}",
            f"Pitch: {self.pitch}",
            f"Bio: {self.bio}",
            f"Line-up: {', '.join(self.line_up)}",
        ]
        if self.formats:
            lines.append(f"Formats offered: {self.formats}")
        lines += [
            f"Sender: {self.sender.name} ({self.sender.role}), {self.sender.email}",
            f"Signature:\n{self.sender.signature}",
        ]
        if self.sender.example_subject:
            lines.append(f"Example subject line: {self.sender.example_subject}")
        lines += [
            f"Target region: {self.targeting.region}",
            f"Venue types: {', '.join(self.targeting.venue_types)}",
        ]
        if self.targeting.avoid:
            lines.append(f"Avoid: {'; '.join(self.targeting.avoid)}")
        if self.do_not_contact:
            lines.append(f"Do not contact: {'; '.join(self.do_not_contact)}")
        if self.links.epk:
            lines.append(f"EPK link: {self.links.epk}")
        if self.links.website:
            lines.append(f"Website: {self.links.website}")
        if self.links.video:
            lines.append(f"Video: {self.links.video}")
        for label, url in self.links.other.items():
            lines.append(f"{label}: {url}")
        if self.current_focus:
            lines.append(f"Current focus: {self.current_focus}")
        if self.availability:
            lines.append(f"Availability: {self.availability}")
        if self.style:
            lines.append(f"Email style rules:\n{self.style}")
        return "\n".join(lines)


def load_profile(path: str | Path) -> BandProfile:
    with open(path, encoding="utf-8") as f:
        return BandProfile.model_validate(yaml.safe_load(f))
