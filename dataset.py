"""Deterministic synthetic support-ticket dataset: short tickets in 5 categories.

    python dataset.py            # writes data/tickets.jsonl (1,000 tickets, seed 42)
"""
import argparse
import json
import random
from pathlib import Path

CATEGORIES = ["billing", "technical", "account", "shipping", "general"]

SLOTS = {
    "product": ["the mobile app", "the web dashboard", "the desktop client", "the API",
                "the browser extension", "the smart thermostat", "the wireless headphones",
                "the standing desk", "the espresso machine", "the fitness tracker"],
    "plan": ["Starter", "Pro", "Team", "Business", "Enterprise"],
    "amount": ["$9.99", "$19", "$29.00", "$49", "$79.50", "$120", "$249.99"],
    "days": ["2", "3", "5", "7", "10", "14"],
    "device": ["iPhone 15", "Pixel 9", "MacBook Air", "Windows 11 laptop", "iPad",
               "Galaxy S24", "Linux desktop"],
    "carrier": ["UPS", "FedEx", "DHL", "USPS", "Royal Mail"],
    "browser": ["Chrome", "Safari", "Firefox", "Edge"],
    "month": ["January", "March", "May", "July", "September", "November"],
    "city": ["Austin", "Toronto", "Leeds", "Denver", "Dublin", "Portland", "Sydney"],
    "error": ["Error 500", "a blank white screen", "'Something went wrong'",
              "a timeout error", "error code E-1042", "a 403 Forbidden message"],
}

TEMPLATES = {
    "billing": [
        "I was charged {amount} twice this month for the {plan} plan. Can you refund the duplicate?",
        "My invoice for {month} shows {amount} but I downgraded to {plan} weeks ago.",
        "Why did my card get billed {amount} after I cancelled my subscription?",
        "I need a VAT invoice for my {month} payment of {amount}.",
        "The promo code didn't apply at checkout and I paid full price ({amount}).",
        "Can I switch from monthly to annual billing on the {plan} plan and get the discount?",
        "I requested a refund of {amount} {days} days ago and it still hasn't shown up on my statement.",
        "My payment keeps getting declined even though the card works everywhere else.",
        "There's a charge of {amount} from you on my statement that I don't recognise.",
        "How do I update the credit card on file before the next renewal in {month}?",
        "We were billed for 12 seats on {plan} but only have 8 users. Please correct the invoice.",
    ],
    "technical": [
        "{product} crashes every time I open the settings page on my {device}.",
        "I keep getting {error} when I try to export a report from {product}.",
        "Sync has been stuck at 0% for {days} days on my {device}.",
        "After the latest update {product} is extremely slow in {browser}.",
        "Notifications stopped working on my {device} since {month}.",
        "The file upload fails with {error} for anything over 10 MB.",
        "{product} won't connect to Wi-Fi after the firmware update.",
        "Search returns no results in {product} even for items I can see in the list.",
        "The integration with our calendar broke and events are now duplicated.",
        "I see {error} in {browser} but it works fine in a private window.",
        "Dark mode makes the text unreadable in {product} on my {device}.",
    ],
    "account": [
        "I can't log in. The password reset email never arrives.",
        "Please change the email address on my account, I no longer have access to the old one.",
        "I lost my phone and can't get past two-factor authentication.",
        "How do I delete my account and all associated data?",
        "My account was locked after too many login attempts. Can you unlock it?",
        "I need to transfer ownership of our {plan} workspace to a colleague.",
        "Someone logged into my account from {city} and it wasn't me.",
        "How do I add a second admin user to our {plan} team?",
        "I signed up with the wrong username. Can it be changed?",
        "Can you merge my two accounts? I accidentally registered twice.",
        "I'd like to export my personal data under GDPR.",
    ],
    "shipping": [
        "Order #{order} was supposed to arrive {days} days ago and tracking hasn't updated.",
        "{carrier} says my package was delivered but I never received it. Order #{order}.",
        "My {product_bare} arrived with a cracked casing. Order #{order}.",
        "Can I change the delivery address for order #{order}? I'm moving to {city}.",
        "I received the wrong item in order #{order}.",
        "Do you ship to {city}, and how long does delivery take?",
        "The tracking number for order #{order} doesn't work on the {carrier} site.",
        "I'd like to return order #{order}. How do I get a return label?",
        "Part of order #{order} is missing from the box.",
        "Can you upgrade order #{order} to express delivery? I need it within {days} days.",
        "My parcel has been stuck at {carrier} customs for {days} days.",
    ],
    "general": [
        "Do you offer a discount for non-profits or students?",
        "What are your customer support hours over the holidays?",
        "Is there a public roadmap? I'd love to know if an offline mode is planned.",
        "Just wanted to say your support team was fantastic last week.",
        "Where can I find documentation for getting started with {product}?",
        "Do you have a partner or affiliate programme?",
        "Is {product} compatible with {device}?",
        "I have a feature suggestion: let us pin favourite items to the top.",
        "Are you hiring? Where can I send my CV?",
        "What's the difference between the {plan} plan and the one below it?",
        "Can someone from sales give us a demo next week?",
    ],
}

OPENERS = ["", "", "", "Hi, ", "Hello, ", "Hey team, ", "URGENT: ", "Good morning. "]
CLOSERS = ["", "", "", " Thanks.", " Please help.", " Please advise ASAP.",
           " Appreciate any help!", " This is really frustrating."]


def generate(n: int = 1000, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    seen, tickets = set(), []
    while len(tickets) < n:
        label = rng.choice(CATEGORIES)
        slots = {k: rng.choice(v) for k, v in SLOTS.items()}
        slots["order"] = str(rng.randint(100000, 999999))
        slots["product_bare"] = slots["product"].removeprefix("the ")
        body = rng.choice(TEMPLATES[label]).format(**slots)
        text = rng.choice(OPENERS) + body[0].upper() + body[1:] + rng.choice(CLOSERS)
        if text in seen:
            continue
        seen.add(text)
        tickets.append({"id": f"t-{len(tickets) + 1:04d}", "text": text, "label": label})
    return tickets


def load_or_create(path: Path, n: int = 1000, seed: int = 42) -> list[dict]:
    if path.exists():
        tickets = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if len(tickets) >= n:
            return tickets[:n]
    tickets = generate(n, seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(t) + "\n" for t in tickets))
    return tickets


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=Path("data/tickets.jsonl"))
    args = ap.parse_args()
    args.out.unlink(missing_ok=True)
    rows = load_or_create(args.out, args.n, args.seed)
    print(f"wrote {len(rows)} tickets to {args.out}")
