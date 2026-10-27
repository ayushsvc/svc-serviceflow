"""Fixed demo-data definitions shared by app.seed_demo and the SLA beat job (no imports from the app)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoReply:
    who: str  # "owner" (the requester) or "tech" (the assignee)
    text: str
    attach: str | None = None  # file kind attached to this public reply


@dataclass(frozen=True)
class DemoTicket:
    title: str
    description: str
    type: str  # INCIDENT, REQUEST, PROBLEM or CHANGE
    priority: int  # 1 Critical, 2 High, 3 Medium, 5 Low
    status: str  # final status, reached through the normal transitions
    owner: str  # "priya" (seeded requester), "req1" or "req2" (tagged)
    assignee: str | None  # "aarav" (seeded technician), "tech1" (tagged) or None
    age_hours: int
    breached: bool = False  # terminal tickets only: solved after the SLA deadline
    replies: tuple[DemoReply, ...] = ()
    attachments: tuple[tuple[str, str], ...] = ()  # (file kind, uploader: "owner" or "admin")


DEMO_TICKETS: tuple[DemoTicket, ...] = (
    DemoTicket(
        "[DEMO] Outlook crashes when opening attachments",
        "Outlook closes unexpectedly every time I open a PDF attachment from an email. Started after last week's update.",
        "INCIDENT", 2, "ASSIGNED", "priya", "aarav", 3,
        replies=(
            DemoReply("tech", "Thanks for reporting this. I am checking the Office update on your device."),
            DemoReply("owner", "It still happens this morning, in case that helps."),
        ),
    ),
    DemoTicket(
        "[DEMO] VPN connection drops every 10 minutes",
        "The VPN disconnects roughly every ten minutes while working from home, which breaks my remote desktop session.",
        "INCIDENT", 1, "PLANNED", "priya", "aarav", 5,
        replies=(DemoReply("tech", "We have reproduced the drop and planned a gateway firmware fix for tonight."),),
    ),
    DemoTicket(
        "[DEMO] Request a laptop for a new joiner",
        "Please provision a standard laptop for a new team member who starts on the first of next month.",
        "REQUEST", 3, "NEW", "priya", None, 2,
    ),
    DemoTicket(
        "[DEMO] Access to the Finance shared folder",
        "I need read access to the Finance shared drive to prepare the quarterly report.",
        "REQUEST", 5, "SOLVED", "priya", "aarav", 12 * 24,
        replies=(DemoReply("tech", "Access has been granted. Please sign out and back in to refresh your permissions."),),
    ),
    DemoTicket(
        "[DEMO] Printer on floor 2 shows offline",
        "The shared printer on the second floor shows as offline for everyone. Screenshot and error report attached.",
        "INCIDENT", 3, "ASSIGNED", "priya", "aarav", 4,
        attachments=(("png", "owner"), ("pdf", "owner")),
    ),
    DemoTicket(
        "[DEMO] Password reset email never arrives",
        "I requested a password reset three times but the email never reaches my inbox, even in spam.",
        "INCIDENT", 2, "NEW", "req1", None, 1,
        replies=(DemoReply("owner", "Adding that I also tried my personal address with the same result."),),
    ),
    DemoTicket(
        "[DEMO] Adobe Acrobat Pro licence request",
        "The design team needs an Adobe Acrobat Pro licence to edit and sign contracts.",
        "REQUEST", 5, "PENDING", "req1", "tech1", 9 * 24,
    ),
    DemoTicket(
        "[DEMO] Wi-Fi unstable in meeting room B",
        "The Wi-Fi in meeting room B drops during video calls. Speed test results attached.",
        "INCIDENT", 3, "CLOSED", "req1", "tech1", 27 * 24,
        attachments=(("txt", "owner"),),
    ),
    DemoTicket(
        "[DEMO] Monitor flickers on the docking station",
        "My external monitor flickers every few seconds when connected through the docking station.",
        "INCIDENT", 5, "NEW", "req2", None, 6,
    ),
    DemoTicket(
        "[DEMO] Recurring disk space alerts on the file server",
        "The file server raised low disk space alerts three times this month. Needs a root-cause investigation.",
        "PROBLEM", 2, "ASSIGNED", "req2", "tech1", 7,
    ),
    DemoTicket(
        "[DEMO] Upgrade the ERP to the latest patch level",
        "Plan and schedule the ERP patch upgrade. Rollout checklist attached for review.",
        "CHANGE", 3, "PLANNED", "req2", "tech1", 5,
        attachments=(("xlsx", "admin"),),
    ),
    DemoTicket(
        "[DEMO] Sales mailbox quota exceeded",
        "The shared sales mailbox is full and customers receive bounce messages.",
        "INCIDENT", 1, "SOLVED", "req2", "aarav", 20 * 24, breached=True,
        replies=(DemoReply("tech", "Quota increased and old items archived. Summary attached.", attach="docx"),),
    ),
    DemoTicket(
        "[DEMO] Install VS Code for the developers",
        "Please install Visual Studio Code on the three new developer workstations.",
        "REQUEST", 5, "CLOSED", "priya", "aarav", 29 * 24,
    ),
    DemoTicket(
        "[DEMO] Teams meetings have an audio echo",
        "Participants hear an echo whenever I speak during Teams meetings, even with a headset.",
        "INCIDENT", 3, "PENDING", "priya", "tech1", 5 * 24, breached=True,
    ),
    DemoTicket(
        "[DEMO] New joiner account setup",
        "Create the email, VPN and ERP accounts for the new joiner in Accounts before the start date.",
        "REQUEST", 2, "NEW", "req1", None, 7,
    ),
)
DEMO_TITLES: frozenset[str] = frozenset(ticket.title for ticket in DEMO_TICKETS)
assert len(DEMO_TITLES) == len(DEMO_TICKETS)
