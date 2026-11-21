# PRODUCT REQUIREMENTS DOCUMENT (PRD)

## AI-Powered Household Shopping Agent

**Version:** 1.0  
**Status:** Proposed – Hackathon MVP  
**Date:** 21 November 2026  
**Product Category:** AI Agent / Commerce / Household Management

---

# 1. Product Overview

## 1.1 Product Vision

Build a voice-first, Hinglish-native household shopping agent that takes care of the work involved in shopping across multiple commerce platforms.

The agent should understand what the household needs, search connected platforms, compare products and prices, optimise shopping baskets, prepare orders for approval, track deliveries and assist with post-purchase issues.

The product should reduce the amount of time and effort users spend managing everyday shopping.

## 1.2 Core Value Proposition

**Tell the agent what you need. It handles the shopping work.**

The user should not have to manually open multiple applications, compare prices, calculate discounts, rebuild baskets or repeatedly contact customer support.

## 1.3 Product Principles

1. **Minimal user involvement:** The agent performs research and preparation independently.
2. **User control:** Purchases and consequential actions require approval.
3. **Balanced optimisation:** Consider price, delivery, availability and convenience together.
4. **Conversational interaction:** Voice-first, with chat as the primary visual interface.
5. **Continuity:** Shopping, order tracking and issue resolution happen in the same conversation.
6. **Reliability over feature quantity:** Prefer one working integration over multiple simulated integrations.

---

# 2. Target Users and Use Cases

## 2.1 Primary User

A household member responsible for purchasing groceries and household essentials.

Typical behaviour:

- Uses multiple commerce platforms.
- Compares product prices and discounts.
- Purchases both individual products and multiple household items.
- Wants to avoid unnecessary shopping effort.
- Uses voice naturally in Hindi, English and Hinglish.
- Expects help when an order has a problem.

## 2.2 Primary Use Cases

| ID | Use case | User goal |
|---|---|---|
| UC-01 | Individual product purchase | Find the best option for one item |
| UC-02 | Household basket purchase | Optimise a multi-item order |
| UC-03 | Offer optimisation | Get the best applicable discount |
| UC-04 | Order tracking | Know when an order will arrive |
| UC-05 | Product complaint | Report a spoiled or damaged item |
| UC-06 | Support escalation | Get assistance resolving an order issue |

---

# 3. Product Experience

## 3.1 Primary Interface

The application will have three primary interaction components:

- Voice input and conversational interaction.
- Chat interface for recommendations, results and approvals.
- Notifications for important events and pending actions.

Voice is the primary way users communicate with the agent. Chat provides the visual representation of its work.

## 3.2 Main Application Sections

### A. Home / Conversation

The primary screen should contain:

- Conversation history.
- Voice interaction control.
- Text input fallback.
- Image attachment option.
- Shopping activity summaries.
- Pending approvals.

### B. Connected Accounts

Users can:

- View supported commerce platforms.
- Connect their accounts.
- View connection status.
- Reconnect expired accounts.
- Disconnect accounts.

### C. Household Preferences

Users can view and manage:

- Preferred brands.
- Product preferences.
- Pack sizes.
- Shopping history.
- Other explicitly saved preferences.

### D. Order and Activity History

Users can access:

- Prepared baskets.
- Approved orders.
- Order status.
- Previous shopping conversations.
- Reported issues and complaint status.

---

# 4. Functional Requirements

## 4.1 Voice and Conversational Intelligence

**Requirement ID: PR-01**

The agent must understand and respond to natural-language shopping instructions.

### Input requirements

- Voice input.
- Text input.
- Image attachments.
- Mixed Hindi-English speech.

### Language examples

User:
"Ghar mein oil khatam ho gaya. Zepto aur Swiggy pe check karo."

User:
"Fortune ka hi lena, koi aur brand mat lena."

User:
"Agar 30 rupaye aur add karne se discount mil raha hai toh chips dekhna."

### Behaviour requirements

- Understand the shopping intent.
- Extract products, quantities and preferences.
- Retain context across turns.
- Handle interruptions and corrections.
- Ask clarification only when necessary.
- Respond naturally in Hinglish.
- Provide a text response alongside voice output where appropriate.

### Acceptance criteria

- The agent correctly identifies requested products.
- It understands common Hinglish shopping instructions.
- It retains product and brand preferences throughout the conversation.
- It can handle corrections without restarting the shopping flow.

---

## 4.2 Account Connection

**Requirement ID: PR-02**

The user must be able to connect supported commerce accounts through an authorised connection experience.

### User flow

1. User opens Connected Accounts.
2. Selects a commerce provider.
3. Selects Connect.
4. Provider authentication window opens.
5. User authenticates and grants required permissions.
6. The application receives the authorised connection.
7. Connected status is displayed.

### Requirements

- Secure token management.
- User-specific authentication.
- Token refresh where supported.
- Reconnection after authentication expiry.
- Account disconnection.
- Clear communication of unavailable integrations.

### Initial provider targets

| Provider | Integration approach |
|---|---|
| Zepto | Official MCP, subject to client and OAuth validation |
| Swiggy Instamart | Official MCP, subject to delegated-auth and production validation |
| Blinkit | Integration feasibility to investigate |
| BigBasket | Integration feasibility to investigate |
| Amazon Now | Integration feasibility to investigate |

**MVP requirement:** At least one real, authenticated commerce integration must work end to end.

---

## 4.3 Product Discovery

**Requirement ID: PR-03**

The agent must search for requested products using connected providers.

### Input

Example:

"Find 1 litre of cooking oil."

### Processing

1. Identify product category.
2. Extract quantity and pack-size requirements.
3. Apply household brand preferences.
4. Query connected commerce providers.
5. Retrieve product information.
6. Normalise product attributes.
7. Return comparable results.

### Output attributes

- Product name.
- Brand.
- Pack size.
- Quantity.
- Price.
- Availability.
- Provider.
- Delivery estimate, when available.

### Acceptance criteria

- Search results contain the requested product or clearly identified alternatives.
- Product quantities and pack sizes are visible.
- Unavailable products are not presented as available.
- Provider errors are communicated transparently.

---

## 4.4 Product Comparison

**Requirement ID: PR-04**

The agent must compare equivalent products across connected platforms.

### Comparison factors

1. Brand.
2. Product specification.
3. Pack size.
4. Unit price.
5. Displayed product price.
6. Availability.
7. Delivery charges.
8. Estimated delivery time.

### Important logic

The agent must compare equivalent products rather than simply selecting the lowest displayed price.

Example:

| Platform | Product | Price |
|---|---|---:|
| Zepto | Cooking oil, 1 L | ₹165 |
| Swiggy | Cooking oil, 1 L | ₹159 |
| Blinkit | Cooking oil, 1 L | ₹160 |

The agent should evaluate the final cost and other relevant trade-offs before making a recommendation.

All displayed figures in demonstrations must be real retrieved values or explicitly labelled illustrative values.

---

## 4.5 Basket Optimisation

**Requirement ID: PR-05**

The agent must support two shopping modes.

### Mode A: Individual Purchase

User needs one product.

Example:
"Oil khatam ho gaya. Best option check karo."

The agent:

1. Searches providers.
2. Compares equivalent products.
3. Evaluates applicable offers.
4. Calculates the final payable amount.
5. Recommends the most suitable option.

### Mode B: Multi-Item Purchase

User needs multiple products.

Example:
"Oil, detergent, milk aur chips order karne hain."

The agent:

1. Searches each requested product.
2. Identifies suitable alternatives.
3. Compares available basket combinations.
4. Evaluates minimum-order conditions.
5. Checks applicable discounts.
6. Calculates total basket cost.
7. Recommends the best combination.

### Optimisation model

The default recommendation strategy is balanced.

The system evaluates:

- Final payable price.
- Product availability.
- Delivery time.
- Delivery charges.
- Brand preferences.
- Pack sizes.
- Promotions.
- Minimum-order requirements.
- Convenience.

The agent must not blindly optimise for the lowest displayed price.

### Add-on recommendation logic

Example:

- Current basket: ₹470.
- Offer threshold: ₹500.
- Additional spend required: ₹30.
- Potential discount: ₹50.

The agent may recommend a relevant ₹30 item if the final transaction produces a genuine benefit.

It must explain:

- Why the additional item is suggested.
- How much additional money is spent.
- What discount is unlocked.
- What the actual net saving is.

The agent must not recommend irrelevant products simply to increase basket size.

### Acceptance criteria

- Individual purchases work independently.
- Multi-item baskets can be prepared.
- Discounts are evaluated against actual eligibility conditions.
- Total cost is calculated correctly.
- Add-on recommendations show their financial impact.
- The recommendation includes relevant trade-offs.

---

## 4.6 Offer and Discount Engine

**Requirement ID: PR-06**

The system must evaluate promotions and discounts available through supported providers.

### Required functionality

- Retrieve available offers where exposed.
- Identify minimum-spend requirements.
- Evaluate coupon eligibility.
- Calculate discount amounts.
- Calculate the final payable amount.
- Identify useful basket adjustments.

### Calculation requirements

The final payable amount should account for:

Product subtotal  
+ Delivery charges  
+ Platform fees  
+ Other applicable charges  
− Verified discounts

### Critical rule

The agent must distinguish:

- Verified offer.
- Estimated saving.
- Ineligible offer.
- Offer requiring further checkout verification.

The final checkout amount must be verified before purchase approval.

---

## 4.7 Cart Preparation and Checkout

**Requirement ID: PR-07**

The agent must prepare a provider-specific cart and request user approval.

### User journey

1. Agent presents recommended products.
2. User reviews the basket.
3. Agent explains the final expected amount.
4. User confirms the basket.
5. Agent prepares or updates the provider cart.
6. Agent verifies the checkout details.
7. User explicitly approves the purchase.
8. Agent submits the order.
9. Agent verifies the result.

### Approval screen requirements

Display:

- Product name.
- Quantity.
- Product price.
- Discount.
- Delivery fee.
- Final payable amount.
- Provider.
- Estimated delivery time.

### Critical rules

- Never place an order without explicit approval.
- Never assume approval from an earlier conversation applies to a changed basket.
- Reconfirm if the payable amount changes materially.
- Avoid duplicate order submissions.
- Verify order creation before reporting success.

---

## 4.8 Order Tracking and Notifications

**Requirement ID: PR-08**

The agent should track available order information and notify users when meaningful events occur.

### Events

| Event | User communication |
|---|---|
| Order confirmed | Confirmation summary |
| Order packed | In-chat status update |
| Order picked up | In-chat status update |
| Delivery delayed | Push notification |
| Payment failed | Immediate notification |
| Order delivered | Delivery summary |
| Order cancelled | Important notification |

The exact events depend on provider data availability.

### Notification principles

- Avoid excessive notifications.
- Notify immediately when user action is required.
- Keep routine updates within the conversation.
- Follow up on unresolved actions when useful.
- Allow notification preferences to be configured.

### Acceptance criteria

- Order status is retrieved from an authorised source.
- Important changes are communicated.
- The agent does not invent delivery estimates.
- Failed tracking requests are communicated clearly.

---

## 4.9 Post-Purchase Issue Resolution

**Requirement ID: PR-09**

The agent must support reporting and handling shopping-related problems.

### Supported issue categories

- Spoiled products.
- Damaged products.
- Missing products.
- Incorrect products.
- Incorrect quantities.
- Delayed deliveries.

### User flow

1. User opens the existing conversation.
2. User describes the issue through voice or text.
3. User attaches a photo where applicable.
4. Agent identifies the relevant order.
5. Agent identifies the affected product.
6. Agent asks for missing information.
7. Agent prepares the complaint.
8. User reviews and approves submission.
9. Agent submits through an available support channel.
10. Agent communicates the result.

### Example

User:
"Ye milk kharab nikla. Photo bhej rahi hoon."

Agent:
"Samajh gayi. Main order details check karti hoon. Aap refund chahte hain ya replacement?"

### Requirements

- Image understanding.
- Order association.
- Complaint categorisation.
- Evidence collection.
- Desired resolution identification.
- Complaint submission where supported.
- Complaint tracking where supported.

The agent must not claim that an image proves spoilage or food safety. It should treat it as user-provided evidence and help report the concern.

---

## 4.10 Customer Support and Outbound Calling

**Requirement ID: PR-10**

The agent should assist with contacting commerce-provider support through available authorised channels.

### Channel priority

1. Official commerce API or support integration.
2. Supported in-app support.
3. Authorised email or callback.
4. Outbound phone call where permitted.

### Outbound call requirements

- User approval before calling.
- Clear identification as an AI assistant.
- Relevant order information available.
- Clear description of the issue.
- No fabricated claims.
- No unauthorised commitments.
- Conversation summary.
- Complaint reference capture where available.
- Follow-up tracking.

### MVP limitation

Outbound support calling is a capability to validate, not a guaranteed integration.

The product must still provide a useful complaint workflow when a platform does not support phone-based assistance.

---

## 4.11 Household Memory

**Requirement ID: PR-11**

The agent should retain useful shopping preferences to reduce repeated instructions.

### Memory attributes

- Preferred brands.
- Preferred product categories.
- Preferred pack sizes.
- Previously selected products.
- Shopping history.
- Explicit corrections.
- Household-specific terminology.

### Example

User:
"Fortune ka hi lena."

The agent should remember the preference if the user permits persistent memory.

### Requirements

- Store user-specific preferences.
- Update preferences after corrections.
- Retrieve relevant preferences during shopping.
- Avoid assuming preferences that have not been established.
- Allow users to view and edit saved information.

Predictive replenishment is excluded from the current MVP.

---

# 5. Agent Architecture and Responsibilities

The agent should not be one unrestricted LLM that performs every operation.

Its responsibilities should be separated into logical components.

| Component | Responsibility |
|---|---|
| Conversation manager | Understands user intent and maintains context |
| Voice interface | Handles speech input and output |
| Shopping planner | Converts requests into shopping tasks |
| Product discovery engine | Searches commerce providers |
| Product normaliser | Matches equivalent products |
| Comparison engine | Evaluates product and provider options |
| Basket optimiser | Calculates suitable basket combinations |
| Offer evaluator | Calculates applicable discounts |
| Commerce executor | Manages carts and approved purchases |
| Order tracker | Retrieves available order updates |
| Support agent | Handles complaints and escalations |
| Memory service | Stores household preferences |

## 5.1 Agent Tool Permissions

| Tool/action | Permission |
|---|---|
| Search products | Autonomous |
| Retrieve prices | Autonomous |
| Compare products | Autonomous |
| Calculate discounts | Autonomous |
| Prepare basket | Autonomous |
| Modify prepared cart | Autonomous within approved task |
| Place order | Explicit approval required |
| Initiate support call | Explicit approval required |
| Submit complaint | Approval required |
| Retrieve order status | Autonomous |
| Store persistent preferences | User-controlled |

The implementation must enforce permissions in the backend, not merely rely on model instructions.

---

# 6. Technical Requirements

## 6.1 Proposed Architecture

### Frontend

- TypeScript-based web application.
- Conversational interface.
- Voice interaction.
- Chat messages and structured shopping cards.
- Image uploads.
- Approval controls.
- Notifications.

### Backend

- Python.
- FastAPI.
- Agent orchestration.
- MCP client integrations.
- Commerce provider adapters.
- Basket optimisation.
- Authentication management.
- Order and complaint management.

### AI

- OpenAI models for reasoning and agent orchestration.
- Speech-to-speech or speech recognition and speech synthesis for voice.
- Vision-capable models for submitted product images.
- Structured outputs for baskets, comparisons and summaries.

### Storage

- PostgreSQL for users, preferences, shopping sessions and order records.
- Redis where needed for short-lived state and background tasks.
- Secure storage for authentication credentials and tokens.

### External integrations

- Zepto MCP.
- Swiggy Instamart MCP.
- Other providers only after integration validation.
- Telephony provider if permitted support calling is implemented.

## 6.2 High-Level Data Flow

1. User provides a voice or text request.
2. Conversation manager interprets the request.
3. Shopping planner creates a structured shopping task.
4. Provider adapters retrieve products.
5. Product normaliser identifies comparable items.
6. Comparison and basket optimisation engines evaluate options.
7. Agent presents recommendations.
8. User approves the basket.
9. Commerce executor performs the authorised operation.
10. Order tracker retrieves updates.
11. User receives relevant notifications.

---

# 7. Core Data Models

The initial implementation should support the following entities.

| Entity | Important attributes |
|---|---|
| User | User ID, locale, notification preferences |
| ConnectedAccount | Provider, authentication status, token reference |
| HouseholdPreference | Brand, category, quantity, pack size |
| ShoppingSession | User request, state, conversation reference |
| Product | Provider ID, name, brand, pack size, price |
| Basket | Provider, products, fees, discounts, total |
| Order | Provider order ID, status, items, total |
| Complaint | Order ID, issue category, evidence, status |
| Notification | User ID, event, delivery status |

Prices, availability and promotions should be treated as time-sensitive information rather than permanent product attributes.

---

# 8. System States and Error Handling

The application should maintain explicit states for shopping operations.

### Shopping session

- IDLE
- UNDERSTANDING_REQUEST
- SEARCHING_PRODUCTS
- COMPARING_OPTIONS
- OPTIMISING_BASKET
- AWAITING_APPROVAL
- EXECUTING_ORDER
- ORDER_CONFIRMED
- FAILED
- CANCELLED

### Complaint session

- ISSUE_REPORTED
- IDENTIFYING_ORDER
- COLLECTING_EVIDENCE
- PREPARING_COMPLAINT
- AWAITING_APPROVAL
- SUBMITTED
- AWAITING_RESOLUTION
- RESOLVED
- FAILED

### Error handling requirements

The system must handle:

- Expired account connections.
- Provider timeouts.
- Missing products.
- Changed prices.
- Unavailable offers.
- Failed cart operations.
- Duplicate order attempts.
- Failed payments.
- Unavailable tracking information.
- Unsupported complaint channels.

Users must receive understandable explanations and a safe next step.

---

# 9. Non-Functional Requirements

## Performance

- Voice interaction should feel conversational.
- Long-running operations should display progress.
- Product results should be retrieved efficiently.
- Background tasks should not block the interface.

## Reliability

- Avoid duplicate orders.
- Verify consequential actions.
- Handle provider failures.
- Maintain consistent shopping-session state.
- Never report success without evidence.

## Security

- Secure account authentication.
- Encrypted credential storage.
- User-specific access controls.
- Secure image and document handling.
- Appropriate retention and deletion policies.
- Audit logs for purchases and support actions.

## Usability

- Minimal interaction.
- Natural Hinglish.
- Clear basket summaries.
- Easy approval and cancellation.
- Text alternatives to voice.
- Accessible error messages.

---

# 10. MVP Scope and Prioritisation

The hackathon should focus on one complete, reliable shopping journey.

## P0 – Required for the core demonstration

1. Voice-based Hinglish interaction.
2. One working commerce account connection.
3. Product discovery.
4. Product comparison.
5. Balanced recommendation.
6. Basket preparation.
7. Final price calculation where supported.
8. Explicit order approval.
9. Actual order execution or a clearly labelled checkout handoff.
10. Order result summary.

## P1 – Valuable extensions

1. Second commerce integration.
2. Household preference memory.
3. Multi-item basket optimisation.
4. Offer and coupon optimisation.
5. Order tracking.
6. Notifications.
7. Photo-based complaint intake.

## P2 – Future capabilities

1. Additional commerce providers.
2. Automated support integrations.
3. Outbound support calling.
4. Advanced complaint resolution.
5. More sophisticated personalisation.

Predictive replenishment remains excluded.

---

# 11. Acceptance Testing

The product should be tested against realistic household scenarios.

| Test | Expected result |
|---|---|
| User requests cooking oil in Hinglish | Agent identifies the correct product |
| User specifies a brand | Agent respects the preference |
| User requests a particular pack size | Agent searches suitable pack sizes |
| Two providers have different prices | Agent compares equivalent products |
| User asks for a multi-item basket | Agent prepares the requested basket |
| Coupon threshold is not met | Agent calculates the additional spend |
| Add-on creates genuine savings | Agent explains the benefit |
| Provider price changes | Agent updates the recommendation |
| User rejects a recommendation | Agent adapts |
| User approves checkout | Agent executes the authorised order |
| Checkout fails | Agent reports the failure |
| Delivery is delayed | Agent communicates the available update |
| User uploads a spoiled-product photo | Agent prepares an appropriate complaint |
| Support channel is unavailable | Agent explains the limitation and offers an available alternative |

---

# 12. Product Success Metrics

The primary metric is:

**User effort per successfully completed shopping task.**

Supporting metrics:

- Time required per shopping task.
- Number of user interactions per order.
- Recommendation acceptance rate.
- Final basket savings after fees.
- Successful order execution rate.
- Failed or duplicate transaction rate.
- Complaint resolution rate.
- Notification usefulness.
- User satisfaction.

The product should not optimise for conversation length, number of AI actions or number of features.

---

# 13. Final MVP Definition

The MVP is a voice-first household shopping agent that can:

1. Understand a natural Hinglish shopping request.
2. Search a real connected commerce platform.
3. Compare available products.
4. Recommend an option using balanced optimisation.
5. Prepare a shopping basket.
6. Explain the final price and applicable offers.
7. Obtain explicit user approval.
8. Execute or hand off the order through a supported flow.
9. Provide the actual outcome to the user.

The broader product includes order tracking and post-purchase support, with integrations added as their availability is verified.

**The defining experience:**

A user should be able to say what they need, let the agent handle the shopping work, review the result and approve the purchase without manually navigating multiple shopping applications.

---

**Document status:** PRD v1.0 – Proposed  
**Next phase:** Technical architecture, API contracts, integration validation and implementation tasks.