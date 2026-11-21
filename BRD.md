# BUSINESS REQUIREMENTS DOCUMENT (BRD)

## Project: AI-Powered Household Shopping Agent
**Version:** 1.0  
**Status:** Draft – Hackathon MVP  
**Date:** 21 November 2026

---

# 1. Executive Summary

The AI-Powered Household Shopping Agent is a voice-first, Hinglish-native AI agent designed to simplify household shopping across multiple commerce platforms.

Today, consumers often manually navigate multiple shopping applications to compare product prices, check availability, evaluate discounts, apply promotional codes and optimise their shopping baskets. This process requires considerable time, effort and repeated decision-making.

The proposed solution acts as a unified household shopping assistant that understands natural-language requests, searches connected commerce platforms, compares available options, optimises baskets and prepares orders for user approval.

The agent also supports order tracking and post-purchase issue resolution, allowing users to report problems through voice, text and images.

The product aims to reduce shopping effort, improve purchasing decisions and provide a consistent shopping experience across different commerce platforms.

**Core proposition:**

An intelligent household shopping agent that handles the work of shopping while keeping the user in control of important decisions.

# 2. Business Problem

## 2.1 Current State

Households commonly use several commerce applications, including:

- Zepto
- Swiggy Instamart
- Blinkit
- BigBasket
- Amazon Now

When purchasing household essentials, users typically:

1. Identify a product they need.
2. Open multiple shopping applications.
3. Search for the same or equivalent products.
4. Compare prices, pack sizes and availability.
5. Check delivery fees, discounts and promotional codes.
6. Determine whether adding another product unlocks an offer.
7. Optimise the basket based on the final payable amount.
8. Place the order.

After purchasing, users may also need to handle delayed deliveries, damaged products, spoiled food, missing items or incorrect orders.

These activities are fragmented across applications and require users to repeatedly perform similar tasks.

## 2.2 Key Business Challenges

| Problem | User impact |
|---|---|
| Multiple shopping applications | Increased navigation and comparison effort |
| Fragmented product information | Difficult purchasing decisions |
| Complex discounts and coupons | Potentially missed savings |
| Manual basket optimisation | Additional time and calculations |
| Repeated shopping instructions | Unnecessary mental effort |
| Fragmented order tracking | Poor visibility |
| Manual complaint handling | Frustrating post-purchase experience |

## 2.3 Opportunity

Create a single intelligent interface that manages these activities across connected shopping platforms.

The product should not merely compare prices. It should understand the household's requirements and recommend the most suitable overall shopping decision.

# 3. Product Vision and Objectives

## 3.1 Product Vision

Build a proactive, voice-first household shopping agent that reduces the time, money and mental effort required to manage everyday shopping.

## 3.2 Business Objectives

1. Reduce the time users spend comparing shopping platforms.
2. Improve household purchasing decisions through balanced optimisation.
3. Reduce repetitive manual shopping activities.
4. Simplify order monitoring and post-purchase issue resolution.
5. Establish a persistent household shopping context through memory and preferences.

## 3.3 User Value Proposition

The product should deliver four primary benefits:

- **Time savings:** Reduce manual comparison and shopping effort.
- **Financial value:** Identify better overall deals and applicable discounts.
- **Reduced mental effort:** Remember preferences and handle repetitive work.
- **Convenience:** Provide shopping and post-purchase assistance through one interface.

# 4. Target Users

## 4.1 Primary User Segment

Households and working families that regularly purchase groceries and household essentials through multiple quick-commerce and e-commerce platforms.

## 4.2 User Characteristics

- Uses multiple shopping applications.
- Compares prices before purchasing.
- Actively checks discounts and promotional offers.
- Purchases both individual products and multi-item baskets.
- Values convenience but does not want to sacrifice reasonable pricing.
- Prefers natural voice interactions over complex application workflows.

## 4.3 Primary Reference Persona

**Household shopper**

A family member responsible for managing household purchases.

Typical behaviour:

- Notices when an essential product runs out.
- Checks multiple platforms for availability and pricing.
- Compares final payable amounts.
- Uses discounts and promotional codes.
- Adds useful products when doing so improves the overall deal.
- Places the order after reviewing the final basket.
- Contacts customer support when something goes wrong.

# 5. Product Scope

## 5.1 In-Scope Capabilities

| Module | Description |
|---|---|
| Voice interaction | Natural-language shopping instructions |
| Multilingual understanding | Hinglish, Hindi and English |
| Commerce connectivity | Connect supported shopping accounts |
| Product discovery | Search products across connected platforms |
| Price comparison | Compare equivalent products and pack sizes |
| Basket optimisation | Optimise individual and multi-item purchases |
| Offer intelligence | Evaluate discounts, coupons and minimum spends |
| Order execution | Prepare baskets and request approval |
| Order tracking | Monitor order status and important changes |
| Post-purchase support | Handle damaged, spoiled, missing or incorrect products |
| Multimodal interaction | Accept text, voice and image inputs |
| Support escalation | Use available support channels, including calls where permitted |
| Household memory | Retain user-approved preferences and shopping context |

## 5.2 Out-of-Scope Capabilities

The following are deliberately excluded from the initial MVP:

- Predictive replenishment notifications.
- Automatic purchasing without user approval.
- Unsolicited outbound voice calls.
- General-purpose personal assistant functionality.
- Autonomous financial decisions.
- Automatic refund or replacement approval.
- Unsupported browser automation or platform access.
- Complex household inventory management.

# 6. Functional Requirements

## FR-01: Voice-First Interaction

**Description:** The system shall allow users to interact with the agent through natural voice conversations.

Requirements:

- Accept spoken instructions.
- Understand Hinglish and Hindi-English code-switching.
- Support English and Hindi interactions.
- Maintain conversational context.
- Allow users to interrupt, correct or modify instructions.
- Provide spoken responses where appropriate.
- Provide a text-based alternative.

Example:

User: "Ghar mein cooking oil khatam ho gaya. Check karo kahan sasta mil raha hai."

Agent: "Haan, connected stores check karti hoon."

## FR-02: Commerce Account Connectivity

**Description:** Users shall be able to connect supported shopping accounts through an authorised connection experience.

Requirements:

- Display supported commerce platforms.
- Initiate the provider's authentication flow.
- Obtain the required user authorisation.
- Securely manage authentication tokens.
- Support reconnection and disconnection.
- Isolate credentials and data between users.

Initial integration targets:

- Zepto
- Swiggy Instamart
- Blinkit
- BigBasket
- Amazon Now

Integration availability and permissions must be verified independently for each platform.

## FR-03: Product Discovery

**Description:** The agent shall search for products across connected commerce platforms.

Requirements:

- Understand natural-language product requests.
- Search connected providers.
- Retrieve product names, pack sizes, prices and availability.
- Identify equivalent products.
- Respect brand preferences.
- Present comparable options.

## FR-04: Cross-Platform Price Comparison

**Description:** The system shall compare available products across connected platforms.

Comparison criteria:

- Product price.
- Quantity and pack size.
- Brand.
- Availability.
- Delivery charges.
- Estimated delivery time.
- Applicable promotions.

The system must avoid comparing products with materially different quantities or specifications as though they were identical.

## FR-05: Basket Optimisation

**Description:** The system shall identify suitable shopping baskets based on the user's requirements.

Two shopping modes are required.

**Mode A – Individual Purchase**

Example: The user needs one litre of cooking oil.

The agent compares available options and recommends the most suitable purchase.

**Mode B – Multi-Item Basket**

Example: The household needs cooking oil, detergent, milk and snacks.

The agent compares the overall basket across providers and identifies the best available purchasing combination.

Basket optimisation must consider:

1. Product price.
2. Pack size and quantity.
3. Brand preferences.
4. Delivery charges.
5. Minimum-order requirements.
6. Coupon eligibility.
7. Discount thresholds.
8. Delivery time.
9. Convenience and reliability.

The default optimisation strategy shall be **balanced**, rather than prioritising the cheapest or fastest option exclusively.

The agent may recommend additional products to unlock offers only when the additions are relevant and the actual financial benefit is positive.

## FR-06: Offer and Discount Intelligence

**Description:** The system shall evaluate available promotions and calculate their effect on the final payable amount.

Requirements:

- Identify applicable coupons.
- Evaluate minimum-spend conditions.
- Check discount eligibility.
- Calculate the final payable amount.
- Identify whether adding an item improves overall value.
- Clearly communicate savings and additional spending.

The agent must distinguish verified discounts from estimated or unavailable promotions.

## FR-07: Cart Preparation and Order Approval

**Description:** The system shall prepare shopping carts and allow users to review and approve purchases.

Requirements:

- Add selected products to the provider's cart.
- Display the final basket.
- Show product quantities and prices.
- Show delivery charges and applicable discounts.
- Display the final payable amount.
- Request explicit user approval before checkout.
- Report the result of the order operation.

The agent shall not independently place an order without explicit approval.

## FR-08: Order Tracking and Notifications

**Description:** The system shall monitor available order information and notify users about meaningful updates.

Requirements:

- Retrieve order status.
- Track available delivery estimates.
- Notify users about significant delays.
- Notify users about failed orders or payment issues.
- Provide delivery confirmation when available.
- Summarise completed orders.

Notification philosophy:

- Important events should trigger notifications.
- Action-required events should be surfaced clearly.
- Routine status changes should generally remain within the conversation.
- Avoid unnecessary or repetitive notifications.

## FR-09: Post-Purchase Issue Resolution

**Description:** Users shall be able to report shopping-related problems using voice, text and images.

Supported issue categories:

- Spoiled products.
- Damaged products.
- Missing products.
- Incorrect products.
- Delayed deliveries.
- Incorrect quantities.
- Other order-related issues.

Requirements:

- Accept images and voice descriptions.
- Retrieve relevant order details.
- Identify the affected product.
- Collect relevant evidence.
- Understand the user's desired resolution.
- Prepare a complaint.
- Submit through supported platform channels.
- Track available complaint updates.
- Provide a summary of the outcome.

Example:

User: "Ye milk kharab nikla. Photo bhej rahi hoon."

Agent: "Samajh gayi. Main order details check karti hoon. Aap refund chahte hain ya replacement?"

## FR-10: Customer Support Escalation and Outbound Calling

**Description:** Where supported, the agent shall help users contact customer support and resolve order-related problems.

Requirements:

- Identify available support channels.
- Prefer supported platform integrations and in-app support.
- Support authorised email or callback workflows.
- Initiate outbound support calls only where permitted.
- Obtain user approval before initiating calls.
- Clearly identify itself as an AI assistant.
- Maintain relevant order context during the interaction.
- Summarise the conversation.
- Record complaint references and next steps.
- Notify users about subsequent updates.

Important constraint:

Outbound support calling is conditional on platform availability, authorisation and applicable telecom requirements. The existence of a shopping MCP integration does not imply that customer-support calling is supported.

## FR-11: Household Memory

**Description:** The system shall maintain useful household shopping context.

Potential memory attributes:

- Preferred brands.
- Preferred product categories.
- Pack sizes.
- Frequently purchased items.
- Shopping history.
- User corrections.
- Explicit household preferences.

Requirements:

- Learn from conversations and authorised purchase information.
- Update preferences when users correct the agent.
- Avoid repeatedly requesting known information.
- Allow users to inspect and modify relevant preferences.
- Avoid treating purchase history as proof of current household stock.

Predictive replenishment based on household memory is excluded from the initial MVP.

# 7. User Experience Requirements

## 7.1 Interaction Philosophy

The product must minimise user involvement.

**The agent does the work. The user makes the important decisions.**

The user should not have to:

- Open multiple applications manually.
- Repeat product requirements.
- Calculate discounts.
- Rebuild baskets across platforms.
- Monitor routine order updates manually.
- Re-enter order information when reporting a complaint.

## 7.2 Interaction Channels

| Channel | Purpose |
|---|---|
| Voice | Give instructions and converse naturally |
| Chat | Review recommendations and approve actions |
| Notifications | Receive important updates |
| Images | Report product and delivery issues |

## 7.3 User Approval Requirements

Explicit approval is required for:

- Placing orders.
- Making purchases.
- Initiating support calls on the user's behalf.
- Other consequential actions not already authorised.

The agent may autonomously perform low-risk research, comparison and preparation within granted permissions.

# 8. High-Level User Journeys

## Journey A: Individual Product Purchase

1. User speaks a shopping request.
2. Agent identifies the product and requirements.
3. Agent searches connected commerce platforms.
4. Agent compares available products.
5. Agent checks applicable offers.
6. Agent recommends the best option.
7. Agent prepares the cart.
8. User reviews and approves.
9. Order is placed.
10. Agent provides order confirmation and tracking updates.

## Journey B: Multi-Item Basket Optimisation

1. User specifies multiple household requirements.
2. Agent searches connected providers.
3. Agent identifies suitable product alternatives.
4. Agent compares overall basket costs.
5. Agent evaluates discounts and minimum spends.
6. Agent suggests relevant add-ons if beneficial.
7. Agent presents the recommended basket.
8. User approves.
9. Agent executes the order.
10. Agent reports the outcome.

## Journey C: Post-Purchase Issue

1. User opens the existing shopping conversation.
2. User explains the problem through voice or text.
3. User uploads supporting images.
4. Agent identifies the relevant order.
5. Agent prepares the complaint.
6. User confirms the intended resolution.
7. Agent uses an available support channel.
8. Where permitted, agent initiates a support call.
9. Agent provides a summary and tracks subsequent updates.

# 9. Business Rules

| ID | Business rule |
|---|---|
| BR-01 | The default shopping optimisation strategy is balanced. |
| BR-02 | Product comparisons must account for pack size and quantity. |
| BR-03 | The final payable amount is more important than the displayed product price alone. |
| BR-04 | Discounts must be evaluated against their actual eligibility conditions. |
| BR-05 | Additional products should only be recommended when relevant and financially beneficial. |
| BR-06 | Purchases require explicit user approval. |
| BR-07 | The agent must not claim an order or complaint was completed without confirmation. |
| BR-08 | The agent must not infer current stock levels solely from purchase history. |
| BR-09 | Notifications should be limited to meaningful updates. |
| BR-10 | Support actions must use authorised channels. |
| BR-11 | User information and account credentials must remain isolated. |
| BR-12 | Recommendations should explain important trade-offs. |

# 10. Non-Functional Requirements

## 10.1 Performance

- Voice interactions should feel responsive.
- Product searches should complete within a reasonable time.
- Multiple provider results should be processed efficiently.
- The interface should clearly communicate when an operation is still running.

## 10.2 Reliability

- Handle provider API failures gracefully.
- Avoid duplicate cart operations and orders.
- Verify checkout outcomes.
- Handle expired authentication tokens.
- Communicate unavailable integrations transparently.

## 10.3 Security and Privacy

- Securely store authentication credentials and tokens.
- Encrypt sensitive information.
- Use user-authorised access.
- Maintain appropriate access controls.
- Support account disconnection.
- Avoid unnecessary collection of personal information.
- Protect uploaded images and order details.

## 10.4 Usability

- Simple conversational interface.
- Natural Hinglish interaction.
- Clear product comparisons.
- Easy-to-understand basket summaries.
- Minimal user actions.
- Accessible text-based alternatives to voice.

# 11. Integration Requirements

| Integration | Intended use | Validation required |
|---|---|---|
| Zepto MCP | Product search, cart and order operations | OAuth flow, permissions and production access |
| Swiggy Instamart MCP | Product search, cart and order operations | Delegated authentication and production access |
| Blinkit | Product and shopping access | Official integration availability |
| BigBasket | Product and shopping access | Supported APIs and account connection |
| Amazon Now | Product and shopping access | Supported shopping integration |
| OpenAI APIs | Voice, language understanding and agent intelligence | Model selection and API implementation |
| Telephony provider | Outbound support calling | Provider permissions and telecom compliance |
| Backend database | Household memory and order context | Security, retention and access controls |

All integrations must be validated before being considered production-ready.

# 12. MVP Prioritisation

The hackathon MVP should prioritise a complete, working shopping experience rather than implementing every proposed capability superficially.

## P0 – Essential

- Voice-based Hinglish interaction.
- At least one working commerce integration.
- Product search.
- Product comparison.
- Basket preparation.
- Balanced optimisation.
- Offer calculation where data is available.
- Explicit checkout approval.
- Order confirmation.

## P1 – Important

- Second commerce integration.
- Household preferences and memory.
- Order tracking.
- Meaningful notifications.
- Photo and voice-based issue reporting.
- Complaint preparation.

## P2 – Extended Capabilities

- Additional commerce platforms.
- Automated support-channel integrations.
- Outbound customer-support calling.
- Advanced complaint follow-up.
- More sophisticated household personalisation.

Predictive replenishment remains outside the current scope.

# 13. Success Metrics

The product should be evaluated on actual user value.

| Metric | Objective |
|---|---|
| Shopping task completion rate | Measure successful shopping journeys |
| Time per shopping task | Measure time saved |
| User interactions per order | Measure reduction in user effort |
| Final basket savings | Measure financial benefit |
| Recommendation acceptance rate | Measure recommendation usefulness |
| Order execution success rate | Measure reliability |
| Complaint resolution rate | Measure post-purchase effectiveness |
| Unnecessary notification rate | Measure interruption and notification quality |

The primary product success metric should be:

**User effort required per successfully completed shopping task.**

# 14. Risks and Dependencies

| Risk | Potential impact | Mitigation |
|---|---|---|
| Commerce APIs unavailable | Limited platform coverage | Start with verified integrations |
| OAuth restrictions | Users unable to connect accounts | Validate redirect and delegated authentication |
| Inconsistent product data | Incorrect comparisons | Normalise product attributes |
| Incorrect promotional information | Poor recommendations | Verify eligibility and final checkout amounts |
| Provider downtime | Failed shopping operations | Graceful fallback |
| Duplicate orders | Financial impact | Idempotency and checkout verification |
| Limited support APIs | Incomplete issue resolution | Channel-independent support engine |
| Telecom restrictions | Outbound calls unavailable | Validate legal and provider requirements |
| Poor Hinglish recognition | Frustrating interactions | Test with real household speech |

# 15. MVP Acceptance Criteria

The MVP will be considered demonstrable when:

1. A user can interact with the agent through voice.
2. The agent understands a natural Hinglish shopping request.
3. The agent can search at least one real connected commerce platform.
4. The agent can retrieve and present relevant product information.
5. The agent can compare product options.
6. The agent can prepare a basket.
7. The agent can explain the final price and available offers.
8. The user can review and approve the order.
9. The system can report the actual order outcome.
10. The system can provide a meaningful order summary.

Additional post-purchase capabilities should be demonstrated where integrations and available time permit.

# 16. Final Product Definition

The AI-Powered Household Shopping Agent is a voice-first, Hinglish-native intelligent shopping assistant that helps households discover products, compare commerce platforms, optimise shopping baskets, place approved orders, track deliveries and resolve post-purchase issues.

Its primary purpose is not to add another shopping application to a user's routine.

It is to reduce the work involved in shopping.

**Product philosophy:**

- Minimise user effort.
- Maximise meaningful value.
- Keep users in control.
- Communicate only when necessary.
- Prefer reliable execution over feature quantity.

---

**Document status:** Draft BRD v1.0  
**Next phase:** Product Requirements Document (PRD), technical architecture and hackathon implementation plan.