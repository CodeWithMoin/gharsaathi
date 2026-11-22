# Inbound delivery-assist line

The Sarvam number `+918064264240` routes inbound calls to committed agent
version 2. The agent behaves as Moin's household shopping/delivery assistant.
“Sir, Zepto”, “Sir, Swiggy”, and equivalent delivery introductions start the
delivery flow. Other callers are offered a transfer to Moin.

Before a controlled hackathon demo, an authenticated user reviews and activates
one delivery context. FastAPI sends the exact recipient, address and landmark
to Sarvam's documented inbound-deployment cohort stream endpoint for one
server-configured demo caller number. The browser never supplies a phone
number, sees the Voice Agents API key, or places a call.

Unknown callers can reach the agent but receive no address. Address disclosure
requires both an approved caller match and an explicit delivery confirmation.
The agent never requests an OTP, payment or tip and never exposes Moin's phone
number; transfers happen through Sarvam only after caller confirmation.

Automated tests mock every Sarvam response. They never dial a phone.
