"use client";

import { useClerk, useUser } from "@clerk/nextjs";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Basket, ChatCircleDots, MagnifyingGlass, Microphone, NotePencil, PaperPlaneRight, ShieldCheck, ShoppingCartSimple, X } from "@phosphor-icons/react";
import { FormEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Brand } from "./brand";
import { DRAWER_PANEL_TRANSITION, DRAWER_SPRING, pushOffset, restoreScrollTarget, viewportInsets, type DrawerName } from "./drawer-push.mjs";
import { BASKET_CHANGED_EVENT, BasketPanel, useAddBasketLine, useSetBasketLineQuantity, type Approval, type ApprovalRequest, type BasketLine, type ProviderName, type ShoppingBasket } from "./basket/BasketPanel";
import { ProductOptions, type Product } from "./product-comparison";
import { conversationSnapshot } from "./conversation-snapshot.mjs";
import { basketSummary, collapseBasketCard, dropResultGroup, emptyTimeline, finishResultGroup, isNearBottom, latestMessageId, moveBasketCard, placeTimelineItems, recordOrderCard, recordResultGroup, restoreTimeline, summaryText, updateOrderCard, withResultGroups, type ChatTimeline, type SummaryStage } from "./chat-timeline.mjs";
import { directSearchOutcome, restoreResultGroups, resultGroupIdentity, type ResultGroup } from "./result-groups.mjs";
import { conversationTitle, relativeChatDate, searchConversations, visibleConversations, type ConversationSummary } from "./chat-list.mjs";
import { stripNonSpeechAnnotations, voiceTurnDisplayText } from "./transcript-annotations.mjs";
import { LiveOrderTracking } from "./order-tracking";
import { DeliveryLinePanel } from "./delivery-line-panel";
import { InputDialog } from "./ui/input-dialog";
import { PersonalizationOnboarding } from "./personalization-onboarding";
import { useWeeklyHistoryAutoSync } from "./purchase-history-import";
import { SupportDialog } from "./support-panel";
import { VoicePanel, useVoicePrewarm, type VoiceAddressFormRequest, type VoiceTranscriptTurn } from "./voice-panel";
import { conversationIntent, conversationReply } from "./conversation-intent.mjs";
import { FirstRunOnboarding } from "./first-run-onboarding";
import { DeliverFirst } from "./deliver-first";
import { AccountSettingsSheet, type SettingsSection } from "./account-settings-sheet";
import { HomeHeader } from "./home-header-bar";
import { useDeliveryLineConfig } from "./delivery-line-config";
import { addressGate, addressLabel, headerAddress } from "./home-header.mjs";
import { RiderCallCard, useRiderCalls } from "./rider-calls";
import { InboundCallCard, useInboundCalls } from "./inbound-calls-ui";
import { placeInboundCalls, riderDirectionsLine } from "./inbound-calls.mjs";
import { placeRiderCalls } from "./rider-assist.mjs";
import { OrderNotificationPrompt, useNotifications } from "./notification-settings";
import { orderFromUrl } from "./push-notifications.mjs";
import type { ZeptoAddressPrefillRequest, ZeptoPrefillResult } from "./zepto-address-form";
import { AddressFormSheet } from "./address-form-sheet";
import { AddressPickerDialog, StoreAddressRow, type AddressPickerStore } from "./address-picker-dialog";
import { disconnectCopy } from "./input-flows.mjs";
import { ConfirmDialog } from "./ui/confirm-dialog";
import type { AddressFormAck } from "./address-form-merge.mjs";
import { ApiError, useApiRequest } from "./authenticated-api";
import { ADDRESS_REMOVED_MESSAGE, applyAssistantEvent } from "./assistant-events.mjs";
import { zeptoAddressSelection } from "./zepto-address-refresh.mjs";
import { OrderConfirmationCard } from "./order-confirmation-card";
import { cartBadgeCount, newOrderCard, orderCardInFlight, orderCardKey, orderedSummary, supersedeOrderCard, supersededOrderText, withOrderResult, type BasketLike, type LiveOrderCard } from "./card-lifecycle.mjs";
import { orderCardIndex, type OrderCardState, type OrderConfirmation } from "./order-confirmation.mjs";
import { OrderStatusCard } from "./order-status-card";
import { orderCardFromPlaced, type OrderStatusCard as OrderStatusCardState } from "./order-status.mjs";
import { PhotoAttachButton, PhotoBubbleImage, PhotoChip, usePhotoAttachment } from "./photo-attachment-ui";
import { attachmentIdFor, canSend, photoBubbleText, photoMessageContent } from "./photo-attachment.mjs";
import { QuickChips, QuickToastView, useQuickActions } from "./quick-chips";
import { headerOrderChip, swapQuery } from "./quick-actions.mjs";
import { SavedListsDialog } from "./saved-lists-dialog";
import { CartSheet } from "./cart-sheet";
import { ComparisonCard } from "./comparison-card-ui";
import { withComparisonCard, type Comparison, type LiveComparisonCard } from "./comparison-card.mjs";

const LEGACY_CHAT_STORAGE_KEY = "gharsaathi:conversation:v1";

type Address = { id: string; address_line: string; category?: string; tag?: string };
type CommerceMode = "demo" | "live";
type ChatMessage = { id: string; role: "user" | "assistant"; content: string; modality: "text" | "voice"; created_at: string; photo_thumbnail?: string | null; photo_preview?: string | null };
type PersistedConversation = { id: string; submitted_query: string; products: Product[]; searched: boolean; messages: ChatMessage[]; results_after_message_id?: string | null; timeline?: unknown; result_groups?: unknown; updated_at: string };
type LegacyConversation = { submittedQuery?: string; products?: Product[]; searched?: boolean };
// incomplete: the assistant failed after saving a basket change; reply is a short fixed line.
type ChatTurnResponse = { conversation_id: string | null; reply: string; ui_events: unknown[]; incomplete?: boolean };
let swiggyConnectionRequest: Promise<{ connected: boolean; mode?: CommerceMode }> | null = null;
let zeptoConnectionRequest: Promise<{ connected: boolean; mode?: CommerceMode }> | null = null;

const suggestions = [
  { label: "Monthly list", query: "Monthly groceries" },
  { label: "Shampoo + batteries", query: "Shampoo and batteries" },
  { label: "Pet supplies", query: "Pet supplies" },
];

export default function Home() {
  return <FirstRunOnboarding>{({ firstName }) => <ShoppingChat userName={firstName} />}</FirstRunOnboarding>;
}

function ShoppingChat({ startWithSetupOpen = false, userName }: { startWithSetupOpen?: boolean; userName: string }) {
  const { signOut } = useClerk();
  const { user } = useUser();
  const apiRequest = useApiRequest();
  const addBasketLine = useAddBasketLine();
  const setBasketLineQuantity = useSetBasketLineQuantity();
  const [voiceOpen, setVoiceOpen] = useState(false);
  const prewarmVoice = useVoicePrewarm();
  const [timeline, setTimeline] = useState<ChatTimeline>(emptyTimeline);
  const [baskets, setBaskets] = useState<Partial<Record<ProviderName, ShoppingBasket>>>({});
  const [basketProvider, setBasketProvider] = useState<ProviderName>("swiggy_instamart");
  const [voiceApproval, setVoiceApproval] = useState<{ provider: ProviderName; request: ApprovalRequest } | null>(null);
  // The one-tap order card prepared by voice or typed chat; it is not saved with the chat.
  // Once its order is placed it becomes a live status card in the (saved) timeline.
  const [orderCard, setOrderCard] = useState<LiveOrderCard | null>(null);
  // The header cart opens a bottom sheet; the chat timeline keeps the basket cards' history.
  const [cartOpen, setCartOpen] = useState(false);
  // At most one live comparison card (compare_options); a new one replaces it. Not saved with the chat.
  const [comparisonCard, setComparisonCard] = useState<LiveComparisonCard | null>(null);
  // In-app order updates (out for delivery, delivered, delayed, cancelled). Browser
  // notifications for the same updates are opt-in (Settings → Notifications).
  const [orderToast, setOrderToast] = useState<string | null>(null);
  const notifications = useNotifications();
  const offerNotificationsRef = useRef(false);
  offerNotificationsRef.current = notifications.offerOrderPrompt;
  const [notificationPrompt, setNotificationPrompt] = useState(false);
  const [chatSaveFailed, setChatSaveFailed] = useState(false);
  const [addingSku, setAddingSku] = useState<string | null>(null);
  const [setupOpen, setSetupOpen] = useState(startWithSetupOpen);
  const [supportOpen, setSupportOpen] = useState(false);
  const [trackingOpen, setTrackingOpen] = useState(false);
  // "Directions" (Settings → Delivery line, and the order card) opens main's directions editor for that store's selected address.
  const [directionsStore, setDirectionsStore] = useState<"swiggy" | "zepto" | null>(null);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [commerceMode, setCommerceMode] = useState<CommerceMode>("demo");
  const [zeptoConnected, setZeptoConnected] = useState<boolean | null>(null);
  useWeeklyHistoryAutoSync({ enabled: commerceMode === "live", swiggyConnected: connected === true, zeptoConnected: zeptoConnected === true });
  const [zeptoSearchReady, setZeptoSearchReady] = useState(false);
  const [zeptoBusy, setZeptoBusy] = useState(false);
  const [zeptoAddresses, setZeptoAddresses] = useState<Address[]>([]);
  const [zeptoAddressId, setZeptoAddressId] = useState("");
  const zeptoAddressIdRef = useRef("");
  zeptoAddressIdRef.current = zeptoAddressId;
  // Shown when the selected Zepto address was deleted in Zepto; tapping it opens the picker.
  const [addressRemovedToast, setAddressRemovedToast] = useState(false);
  // One Add delivery address popup for voice, typed chat and the manual button.
  const [addressPopupOpen, setAddressPopupOpen] = useState(false);
  const [addressPrefillRequest, setAddressPrefillRequest] = useState<ZeptoAddressPrefillRequest | null>(null);
  // "Choose delivery address" popup (one store at a time) and the Disconnect confirmation.
  const [addressPicker, setAddressPicker] = useState<AddressPickerStore | null>(null);
  const [disconnectConfirm, setDisconnectConfirm] = useState<{ store: "swiggy" | "zepto"; open: boolean }>({ store: "swiggy", open: false });
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [addressId, setAddressId] = useState("");
  // Set once each store's saved addresses have been read, so "Where should we deliver?" never flashes while loading.
  const [swiggyAddressesLoaded, setSwiggyAddressesLoaded] = useState(false);
  const [zeptoAddressesLoaded, setZeptoAddressesLoaded] = useState(false);
  const [storeConnectError, setStoreConnectError] = useState("");
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatDrawerOpen, setChatDrawerOpen] = useState(false);
  const [accountSettingsOpen, setAccountSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>(null);
  const [recentChats, setRecentChats] = useState<ConversationSummary[]>([]);
  const [chatSearch, setChatSearch] = useState("");
  const [recentChatsLoading, setRecentChatsLoading] = useState(false);
  const [loading, setLoading] = useState<"connection" | "addresses" | "search" | "assistant" | "disconnect" | null>("connection");
  const [chatFailure, setChatFailure] = useState<{ query: string; message: string } | null>(null);
  const [chatHydrated, setChatHydrated] = useState(false);
  const [message, setMessage] = useState("Checking your Swiggy connection…");
  const addressRequestRef = useRef<Promise<void> | null>(null);
  const zeptoAddressRequestRef = useRef<Promise<void> | null>(null);
  const searchRequestsRef = useRef(new Map<string, Promise<void>>());
  const lastSearchRef = useRef<{ key: string; at: number } | null>(null);
  const conversationSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const conversationSaveTimerRef = useRef<number | null>(null);
  const pendingConversationSaveRef = useRef<(() => void) | null>(null);
  const chatMessagesRef = useRef<ChatMessage[]>([]);
  // Rider assist (beta): calls the AI answered show as a toast and a chat card.
  const latestMessageIdRef = useRef<string | null>(null);
  latestMessageIdRef.current = latestMessageId(chatMessages);
  const riderCalls = useRiderCalls({ latestMessageIdRef, onToast: setOrderToast });
  const deliveryLine = useDeliveryLineConfig();
  // Calls to the Sarvam inbound delivery line: a card next to the order, and a status line.
  const inboundCalls = useInboundCalls({ onToast: setOrderToast });
  const conversationIdRef = useRef<string | null>(null);
  // Set when the saved chat could not be loaded, so the first save starts a new chat instead of overwriting it.
  const startFreshConversationRef = useRef(false);
  const selectedAddress = useMemo(() => addresses.find((address) => address.id === addressId), [addresses, addressId]);
  const selectedZeptoAddress = useMemo(() => zeptoAddresses.find((address) => address.id === zeptoAddressId), [zeptoAddresses, zeptoAddressId]);
  const closeVoice = useCallback(() => setVoiceOpen(false), []);
  // Shop from a photo: one pending attachment, sent with the next typed turn or read by voice.
  const photo = usePhotoAttachment();
  const consumePhoto = photo.consumed;
  const handlePhotoRead = useCallback(({ attachmentId }: { attachmentId: string }) => consumePhoto(attachmentId), [consumePhoto]);
  const basketProviderRef = useRef<ProviderName>(basketProvider);
  useEffect(() => { basketProviderRef.current = basketProvider; }, [basketProvider]);
  const followScrollRef = useRef(true);
  const scrollAnimatingRef = useRef(false);
  // The fixed composer dock (chips + photo chip + composer) changes height; the chat keeps
  // that much space at the bottom so the newest card is never hidden underneath it.
  const dockRef = useRef<HTMLDivElement | null>(null);
  const chatSectionRef = useRef<HTMLElement | null>(null);
  const [dockHeight, setDockHeight] = useState(0);
  // What each store's card shows, read when a card collapses into its summary.
  const basketsRef = useRef<Partial<Record<ProviderName, ShoppingBasket>>>({});
  const billStageRef = useRef<Partial<Record<ProviderName, SummaryStage>>>({});
  const pendingBillStageRef = useRef<Partial<Record<ProviderName, SummaryStage>>>({});
  const handleBasketChange = useCallback((next: ShoppingBasket) => {
    basketsRef.current = { ...basketsRef.current, [next.provider]: next };
    setBaskets(basketsRef.current);
    // A changed basket makes an open order card for the old revision out of date at once.
    setOrderCard((current) => supersedeOrderCard(current, next));
  }, []);
  // The store's live card moves to now; the card it leaves becomes a summary.
  const showBasketAtLatest = useCallback((provider: ProviderName, stage: SummaryStage = "updated") => {
    const previous = basketSummary(basketsRef.current[provider], billStageRef.current[provider]);
    const afterMessageId = latestMessageId(chatMessagesRef.current);
    setTimeline((current) => moveBasketCard(current, { provider, afterMessageId, previous }));
    billStageRef.current = { ...billStageRef.current, [provider]: stage };
    setBasketProvider(provider);
  }, []);
  const handleBasketTouched = useCallback((provider?: ProviderName, basket?: BasketLike) => {
    const store = provider ?? basketProviderRef.current;
    // An assistant edit changes the revision: an open order card for the old one is out of date now,
    // not only after the basket panel re-reads the basket.
    if (basket) setOrderCard((current) => supersedeOrderCard(current, basket));
    window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
    const stage = pendingBillStageRef.current[store] ?? "updated";
    pendingBillStageRef.current = { ...pendingBillStageRef.current, [store]: undefined };
    showBasketAtLatest(store, stage);
  }, [showBasketAtLatest]);
  const handleBillStep = useCallback((provider: ProviderName, stage: "bill_ready" | "bill_approved") => {
    showBasketAtLatest(provider, stage);
  }, [showBasketAtLatest]);
  const handleVoiceApproval = useCallback((provider: ProviderName, request: ApprovalRequest) => {
    pendingBillStageRef.current = { ...pendingBillStageRef.current, [provider]: "bill_ready" };
    setVoiceApproval({ provider, request });
  }, []);
  const handleComparison = useCallback((comparison: Comparison) => {
    followScrollRef.current = true;
    setComparisonCard({ comparison, afterMessageId: latestMessageId(chatMessagesRef.current) });
  }, []);
  const handleOrderConfirmation = useCallback((confirmation: OrderConfirmation) => {
    followScrollRef.current = true;
    // The newest card replaces any earlier one, at the newest point in the chat.
    setOrderCard(newOrderCard(confirmation, latestMessageId(chatMessagesRef.current)));
  }, []);
  // The store confirmed an order: the basket closed with it (server-side, one
  // transaction). Its card becomes "Ordered · 1 item · ₹55", linked to the live
  // status card, the cart badge drops to 0 and the next add starts a new basket.
  const recordPlacedOrder = useCallback((order: { orderId: string; store: string; provider: ProviderName; afterMessageId: string | null; itemCount: number | null; payable: string | null; payablePaise: number | null; itemNames: string[]; tracking: unknown }) => {
    const placed = orderCardFromPlaced({ orderId: order.orderId, store: order.store, afterMessageId: order.afterMessageId, seq: 0, itemCount: order.itemCount, payable: order.payable, itemNames: order.itemNames, tracking: order.tracking });
    const ordered = basketsRef.current[order.provider];
    if (ordered) basketsRef.current = { ...basketsRef.current, [order.provider]: { ...ordered, status: "ordered" } };
    setBaskets(basketsRef.current);
    const summary = order.itemCount !== null && order.payablePaise !== null ? orderedSummary({ itemCount: order.itemCount, payablePaise: order.payablePaise, orderId: order.orderId }) : { stage: "ordered" as const, itemCount: order.itemCount, itemTotalPaise: order.payablePaise, orderId: order.orderId };
    setTimeline((current) => recordOrderCard(collapseBasketCard(current, order.provider, summary), placed));
    billStageRef.current = { ...billStageRef.current, [order.provider]: undefined };
    setVoiceApproval((current) => current?.provider === order.provider ? null : current);
    window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
  }, []);
  const handleOrderCardResult = useCallback((card: LiveOrderCard, state: OrderCardState) => {
    setOrderCard((current) => withOrderResult(current, card.confirmation.id, state.status));
    if (state.status !== "placed" || card.confirmation.provider !== "zepto") return;
    recordPlacedOrder({
      orderId: state.orderId,
      store: card.confirmation.store,
      provider: card.confirmation.provider,
      afterMessageId: card.afterMessageId,
      itemCount: card.confirmation.itemCount,
      payable: card.confirmation.payable,
      payablePaise: card.confirmation.payablePaise,
      itemNames: card.confirmation.items.map((item) => item.name),
      tracking: state.tracking,
    });
    setOrderCard((current) => current?.confirmation.id === card.confirmation.id ? null : current);
    // Asked once, after a placed order, and only by our own prompt (never on load).
    if (offerNotificationsRef.current) setNotificationPrompt(true);
  }, [recordPlacedOrder]);
  // A tapped notification opens "/?order=<id>" (or messages an open tab): show that order.
  useEffect(() => {
    if (!chatHydrated) return;
    const reveal = (url: string) => {
      const orderId = orderFromUrl(url);
      if (!orderId) return;
      window.setTimeout(() => document.getElementById(`order-status-${orderId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 300);
    };
    const current = new URL(window.location.href);
    if (current.searchParams.has("order")) {
      reveal(`${current.pathname}${current.search}`);
      current.searchParams.delete("order");
      window.history.replaceState(null, "", `${current.pathname}${current.search}${current.hash}`);
    }
    if (!("serviceWorker" in navigator)) return;
    const onMessage = (event: MessageEvent) => {
      if (event.data?.type === "gharsaathi:open" && typeof event.data.url === "string") reveal(event.data.url);
    };
    navigator.serviceWorker.addEventListener("message", onMessage);
    return () => navigator.serviceWorker.removeEventListener("message", onMessage);
  }, [chatHydrated]);
  // On app load: one read-only check of any earlier order whose outcome is unknown.
  const pendingOrdersCheckedRef = useRef(false);
  useEffect(() => {
    if (!chatHydrated || pendingOrdersCheckedRef.current) return;
    pendingOrdersCheckedRef.current = true;
    void apiRequest<{ orders: { status: string; store: string; provider_order_id: string | null; provider_order_code?: string | null; payable: { paise: number; display: string }; tracking?: unknown }[] }>("/api/orders/reconcile-pending", { method: "POST" })
      .then(({ orders }) => {
        for (const order of orders) {
          if (order.status === "confirmed" && order.provider_order_id) {
            recordPlacedOrder({ orderId: order.provider_order_id, store: order.store, provider: "zepto", afterMessageId: latestMessageId(chatMessagesRef.current), itemCount: null, payable: order.payable.display, payablePaise: order.payable.paise, itemNames: [], tracking: order.tracking ?? null });
            setOrderToast(`${order.store} confirmed your earlier order${order.provider_order_code ? ` #${order.provider_order_code}` : ""}.`);
          } else if (order.status === "outcome_unknown") {
            setOrderToast(`${order.store} hasn't confirmed an earlier order (${order.payable.display}). Check the ${order.store} app before ordering again.`);
          }
        }
      })
      .catch(() => { /* A failed read changes nothing; the card and the assistant can check later. */ });
  }, [apiRequest, chatHydrated, recordPlacedOrder]);
  const handleOrderStatusChange = useCallback((card: OrderStatusCardState) => {
    setTimeline((current) => updateOrderCard(current, card.orderId, card));
  }, []);
  useEffect(() => {
    if (!orderToast) return;
    const timer = window.setTimeout(() => setOrderToast(null), 5000);
    return () => window.clearTimeout(timer);
  }, [orderToast]);
  const handleApprovalChange = useCallback((approval: Approval | null) => {
    if (!approval) setVoiceApproval(null);
  }, []);
  const hideBasket = useCallback((provider: ProviderName) => {
    const summary = basketSummary(basketsRef.current[provider], billStageRef.current[provider]);
    setTimeline((current) => collapseBasketCard(current, provider, summary));
    setVoiceApproval((current) => current?.provider === provider ? null : current);
  }, []);
  // The backend decides whether the form may open (address_form.open vs .unavailable);
  // the page never second-guesses it and always reports what it showed.
  const addressAckResolversRef = useRef<((result: { form_visible: boolean; reason?: string; filled_fields?: string[]; kept_user_fields?: string[] }) => void)[]>([]);
  const handleVoiceAddressForm = useCallback((request: VoiceAddressFormRequest) => {
    if ("unavailable" in request) {
      if (request.unavailable === "zepto_not_connected") setSetupOpen(true);
      return;
    }
    return new Promise<{ form_visible: boolean; reason?: string; filled_fields?: string[]; kept_user_fields?: string[] }>((resolve) => {
      addressAckResolversRef.current.push(resolve);
      setAddressPrefillRequest((current) => ({ key: (current?.key ?? 0) + 1, values: request.prefill }));
      setAddressPopupOpen(true);
    });
  }, []);
  const handleAddressPrefillApplied = useCallback((result: ZeptoPrefillResult) => {
    const resolvers = addressAckResolversRef.current;
    addressAckResolversRef.current = [];
    for (const resolve of resolvers) resolve(result.visible ? { form_visible: true, filled_fields: result.filled, kept_user_fields: result.kept } : { form_visible: false, reason: "render_failed" });
  }, []);
  const closeAddressPopup = useCallback(() => {
    setAddressPopupOpen(false);
    setAddressPrefillRequest(null);
  }, []);
  // A voice or typed switch to another saved address; the server already saved it.
  const handleAddressSelected = useCallback(({ provider, addressId: nextAddressId }: { provider: ProviderName; addressId: string }) => {
    if (provider === "zepto") {
      setZeptoAddressId(nextAddressId);
      setZeptoSearchReady(true);
      setAddressRemovedToast(false);
    } else setAddressId(nextAddressId);
  }, []);
  // Typed chat cannot confirm the form within its own turn; the next turn carries the report.
  const pendingTypedAcksRef = useRef<(AddressFormAck & { ack_id?: string })[]>([]);
  // Each searched item gets its own result group; searching it again replaces the group.
  const handleVoiceResults = useCallback((searchQuery: string, foundProducts: Product[], item: { itemKey?: string; itemLabel?: string }) => {
    const identity = resultGroupIdentity({ ...item, query: searchQuery, products: foundProducts });
    const afterMessageId = latestMessageId(chatMessagesRef.current);
    setSubmittedQuery(searchQuery);
    setTimeline((current) => recordResultGroup(current, { ...identity, query: searchQuery, products: foundProducts, status: "done", afterMessageId }));
    setMessage(`${foundProducts.length} ${commerceMode === "demo" ? "demo" : "live"} options found.`);
  }, [commerceMode]);
  const handleVoiceTranscript = useCallback((turn: VoiceTranscriptTurn) => {
    const content = voiceTurnDisplayText(turn);
    setChatMessages((current) => {
      const index = current.findIndex((item) => item.id === turn.id);
      if (!content) return current;
      if (index === -1) {
        const next = [...current, { id: turn.id, role: turn.role, content, modality: "voice" as const, created_at: new Date().toISOString() }];
        chatMessagesRef.current = next;
        return next;
      }
      const next = [...current];
      next[index] = { ...next[index], role: turn.role, content };
      chatMessagesRef.current = next;
      return next;
    });
  }, []);

  // Quick chips and saved lists: each tap is the user's own turn in the timeline.
  const appendMessage = useCallback((role: ChatMessage["role"], content: string) => {
    followScrollRef.current = true;
    const next = [...chatMessagesRef.current, { id: `quick-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}-${role}`, role, content, modality: "text" as const, created_at: new Date().toISOString() }];
    chatMessagesRef.current = next;
    setChatMessages(next);
  }, []);
  const quick = useQuickActions({
    ready: chatHydrated,
    addresses: () => ({ swiggy_address_id: connected === true && addressId ? addressId : null, zepto_address_id: zeptoConnected === true && zeptoAddressId ? zeptoAddressId : null }),
    onUserAction: (text) => appendMessage("user", text),
    onAssistantNote: (text) => appendMessage("assistant", text),
    onBaskets: (changed) => { for (const next of changed) { handleBasketChange(next); showBasketAtLatest(next.provider); } },
    onAsk: (text) => void runSearch(text),
  });
  const [nowTick, setNowTick] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNowTick(Date.now()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  const orderChip = headerOrderChip(timeline.orders ?? [], nowTick);
  // "Swap" on a basket line: search that item; adding an option from those results replaces the line.
  const swapRef = useRef<{ provider: ProviderName; variantId: string; name: string; groupKey: string } | null>(null);
  const startSwap = (provider: ProviderName, line: BasketLine) => {
    followScrollRef.current = true;
    const swapText = swapQuery(line);
    swapRef.current = { provider, variantId: line.ref.variant_id, name: line.name, groupKey: resultGroupIdentity({ query: swapText }).key };
    appendMessage("user", `Swap ${line.name}`);
    void runDirectSearch(swapText);
  };

  const applyConversation = useCallback((conversation: PersistedConversation) => {
    const restoredIntent = conversationIntent(conversation.submitted_query);
    const localReply = restoredIntent === "connection_status"
      ? "Manage stores mein current Swiggy aur Zepto connection status dikh raha hai."
      : conversationReply(conversation.submitted_query, { swiggyConnected: false, zeptoConnected: false });
    const restoredMessages = conversation.messages?.length
      ? conversation.messages
        .map((item) => item.modality === "voice" ? { ...item, content: stripNonSpeechAnnotations(item.content) } : item)
        .filter((item) => item.content)
      : conversation.submitted_query
        ? [{ id: `migrated-${conversation.id}`, role: "user" as const, content: conversation.submitted_query, modality: "text" as const, created_at: conversation.updated_at }]
        : [];
    const repairedMessages = localReply && restoredMessages.at(-1)?.content !== localReply
      ? [...restoredMessages, { id: `local-reply-${Date.now().toString(36)}`, role: "assistant" as const, content: localReply, modality: "text" as const, created_at: new Date().toISOString() }]
      : restoredMessages;
    const fallbackResultAnchor = [...repairedMessages].reverse().find((item) => item.role === "user")?.id ?? null;
    setSubmittedQuery(localReply ? "" : conversation.submitted_query);
    setChatMessages(repairedMessages);
    chatMessagesRef.current = repairedMessages;
    conversationIdRef.current = conversation.id;
    const restoredTimeline = restoreTimeline(conversation.timeline);
    const restoredGroups = localReply ? [] : restoreResultGroups<Product>({
      resultGroups: conversation.result_groups,
      products: conversation.products,
      searched: conversation.searched,
      submittedQuery: conversation.submitted_query,
      resultsAfterMessageId: conversation.results_after_message_id ?? fallbackResultAnchor,
      resultsSeq: restoredTimeline.resultsSeq,
    });
    setTimeline(withResultGroups(restoredTimeline, restoredGroups));
    const newest = [...restoredTimeline.live].sort((a, b) => b.seq - a.seq)[0];
    if (newest) setBasketProvider(newest.provider);
    billStageRef.current = {};
    pendingBillStageRef.current = {};
    setVoiceApproval(null);
    setOrderCard(null);
    setComparisonCard(null);
    followScrollRef.current = true;
  }, []);

  const loadRecentChats = useCallback(async () => {
    setRecentChatsLoading(true);
    try {
      setRecentChats(visibleConversations(await apiRequest<ConversationSummary[]>("/api/conversations?limit=30&exclude_empty=true")));
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not load recent chats.");
    } finally {
      setRecentChatsLoading(false);
    }
  }, [apiRequest]);

  const loadAddresses = useCallback(async (refresh = false) => {
    if (addressRequestRef.current) return addressRequestRef.current;

    const request = (async () => {
      setLoading("addresses");
      try {
        const data = await apiRequest<{ addresses: Address[]; resolution: { default_address_id?: string }; needs_refresh?: boolean }>(`/api/swiggy/addresses${refresh ? "?refresh=true" : ""}`);
        setAddresses(data.addresses);
        const preferred = data.resolution.default_address_id;
        if (preferred && data.addresses.some((item) => item.id === preferred)) setAddressId(preferred);
        else if (data.addresses.length === 1) setAddressId(data.addresses[0].id);
        setMessage(data.addresses.length ? (commerceMode === "demo" ? "Demo catalogue is ready. No provider calls will be made." : "Saved address ready. Reload did not contact Swiggy.") : data.needs_refresh ? "Tap Refresh once to fetch and save your Swiggy addresses." : "No saved Swiggy address found.");
    } catch (cause) {
      const text = cause instanceof Error ? cause.message : "Could not load addresses.";
      setMessage(text);
      if (cause instanceof ApiError && cause.status === 401) setConnected(false);
      } finally {
        setLoading(null);
        setSwiggyAddressesLoaded(true);
      }
    })();
    addressRequestRef.current = request;
    void request.finally(() => {
      if (addressRequestRef.current === request) addressRequestRef.current = null;
    });
    return request;
  }, [apiRequest, commerceMode]);

  // `auto` asks the backend for its bounded Zepto refresh: "load" (stale after 30 min) or "picker" (5 min).
  const loadZeptoAddresses = useCallback(async (refresh = false, auto?: "load" | "picker") => {
    if (zeptoAddressRequestRef.current) return zeptoAddressRequestRef.current;
    const request = (async () => {
      setZeptoBusy(true);
      try {
        const query = refresh ? "?refresh=true" : auto ? `?auto=${auto}` : "";
        const data = await apiRequest<{ addresses: Address[]; resolution: { default_address_id?: string }; needs_refresh?: boolean; selection_removed?: boolean }>(`/api/zepto/addresses${query}`);
        setZeptoAddresses(data.addresses);
        const selection = zeptoAddressSelection(data, zeptoAddressIdRef.current);
        setZeptoAddressId(selection.selectedId);
        if (selection.selectionRemoved) setAddressRemovedToast(true);
        setZeptoSearchReady(data.addresses.length > 0);
        if (refresh && data.addresses.length) setMessage("Zepto addresses saved. Reload will use this snapshot.");
        else if (data.needs_refresh) setMessage("Tap Refresh from Zepto once to save your delivery addresses.");
      } catch (cause) {
        setZeptoSearchReady(false);
        setMessage(cause instanceof Error ? cause.message : "Could not load Zepto addresses.");
      } finally {
        setZeptoBusy(false);
        setZeptoAddressesLoaded(true);
      }
    })();
    zeptoAddressRequestRef.current = request;
    void request.finally(() => {
      if (zeptoAddressRequestRef.current === request) zeptoAddressRequestRef.current = null;
    });
    return request;
  }, [apiRequest]);
  // Opening the Zepto picker refreshes a snapshot older than 5 minutes (bounded on the server).
  useEffect(() => {
    if (addressPicker === "zepto" && zeptoConnected === true && commerceMode === "live") void loadZeptoAddresses(false, "picker");
  }, [addressPicker, zeptoConnected, commerceMode, loadZeptoAddresses]);
  // The selected Zepto address was deleted in Zepto: clear it and fetch the current list.
  const handleAddressRemoved = useCallback((provider: ProviderName) => {
    if (provider !== "zepto") return;
    setZeptoAddressId("");
    setAddressRemovedToast(true);
    void loadZeptoAddresses(false);
  }, [loadZeptoAddresses]);

  useEffect(() => {
    let active = true;
    let generation = 0;
    const checkConnection = (force = false) => {
      const currentGeneration = ++generation;
      if (force) swiggyConnectionRequest = null;
      swiggyConnectionRequest ??= apiRequest<{ connected: boolean; mode?: CommerceMode }>("/api/swiggy/connection");
      void swiggyConnectionRequest
        .then((data) => {
          if (!active || currentGeneration !== generation) return;
          setConnected(data.connected);
          const mode = data.mode ?? "live";
          setCommerceMode(mode);
          setMessage(mode === "demo" ? "Demo mode is ready. No commerce API or MCP calls." : data.connected ? "Swiggy is connected." : "Connect Swiggy for live product search.");
          if (data.connected) void loadAddresses();
        })
        .catch((cause: unknown) => {
          if (!active || currentGeneration !== generation) return;
          setConnected(false);
          setMessage(cause instanceof Error ? cause.message : "Could not check connection.");
        })
        .finally(() => {
          if (active && currentGeneration === generation) {
            setLoading((value) => value === "connection" ? null : value);
          }
        });
    };
    const callbackReturned = new URL(window.location.href).searchParams.get("swiggy") === "connected";
    if (callbackReturned) {
      setSetupOpen(true);
      const url = new URL(window.location.href);
      url.searchParams.delete("swiggy");
      window.history.replaceState({}, "", url);
    }
    checkConnection(callbackReturned);
    const handleFocus = () => checkConnection(true);
    window.addEventListener("focus", handleFocus);
    return () => {
      active = false;
      window.removeEventListener("focus", handleFocus);
    };
  }, [apiRequest, loadAddresses]);

  useEffect(() => {
    let active = true;
    let generation = 0;
    const checkConnection = (force = false) => {
      const currentGeneration = ++generation;
      if (force) zeptoConnectionRequest = null;
      zeptoConnectionRequest ??= apiRequest<{ connected: boolean; mode?: CommerceMode }>("/api/zepto/connection");
      void zeptoConnectionRequest
        .then((data) => {
          if (!active || currentGeneration !== generation) return;
          setZeptoConnected(data.connected);
          if (data.connected) void loadZeptoAddresses(false, "load");
        })
        .catch(() => {
          if (active && currentGeneration === generation) setZeptoConnected(false);
        });
    };
    const callbackReturned = new URL(window.location.href).searchParams.get("zepto") === "connected";
    if (callbackReturned) {
      setSetupOpen(true);
      const url = new URL(window.location.href);
      url.searchParams.delete("zepto");
      window.history.replaceState({}, "", url);
    }
    checkConnection(callbackReturned);
    const handleFocus = () => checkConnection(true);
    window.addEventListener("focus", handleFocus);
    return () => {
      active = false;
      window.removeEventListener("focus", handleFocus);
    };
  }, [apiRequest, loadZeptoAddresses]);

  useEffect(() => {
    let active = true;
    void apiRequest<PersistedConversation | null>("/api/conversations/current")
      .then((conversation) => {
        if (!active) return;
        if (conversation) {
          const earlyTurns = chatMessagesRef.current.filter((item) => !conversation.messages?.some((saved) => saved.id === item.id));
          applyConversation(earlyTurns.length ? { ...conversation, messages: [...(conversation.messages ?? []), ...earlyTurns] } : conversation);
          window.localStorage.removeItem(LEGACY_CHAT_STORAGE_KEY);
          return;
        }
        const legacyRaw = window.localStorage.getItem(LEGACY_CHAT_STORAGE_KEY);
        if (!legacyRaw) return;
        const legacy = JSON.parse(legacyRaw) as LegacyConversation;
        if (!legacy.submittedQuery?.trim()) return;
        const migratedProducts = Array.isArray(legacy.products) ? legacy.products : [];
        const migratedSearched = legacy.searched === true;
        setSubmittedQuery(legacy.submittedQuery);
        const migratedMessages: ChatMessage[] = [{ id: `legacy-${Date.now().toString(36)}`, role: "user", content: legacy.submittedQuery, modality: "text", created_at: new Date().toISOString() }];
        const migratedGroups = restoreResultGroups<Product>({ products: migratedProducts, searched: migratedSearched, submittedQuery: legacy.submittedQuery, resultsAfterMessageId: migratedMessages[0].id });
        setTimeline((current) => withResultGroups(current, migratedGroups));
        setChatMessages(migratedMessages);
        void apiRequest<PersistedConversation>("/api/conversations/current", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ submitted_query: legacy.submittedQuery, products: migratedProducts, searched: migratedSearched, messages: migratedMessages }),
        }).then(() => window.localStorage.removeItem(LEGACY_CHAT_STORAGE_KEY));
      })
      .catch((cause: unknown) => {
        if (!active) return;
        startFreshConversationRef.current = true;
        setMessage(cause instanceof Error ? cause.message : "Could not restore this chat.");
      })
      .finally(() => { if (active) setChatHydrated(true); });
    return () => { active = false; };
  }, [apiRequest, applyConversation]);

  useEffect(() => {
    if (!chatDrawerOpen) return;
    void loadRecentChats();
  }, [chatDrawerOpen, loadRecentChats]);

  useEffect(() => {
    let active = true;
    for (const provider of ["swiggy_instamart", "zepto"] as const) {
      void apiRequest<ShoppingBasket>(`/api/basket?provider=${provider}`)
        .then((savedBasket) => {
          if (active && !basketsRef.current[provider]) handleBasketChange(savedBasket);
        })
        .catch(() => {
          // The basket panel will show a detailed error if the user opens it.
        });
    }
    return () => { active = false; };
  }, [apiRequest, handleBasketChange]);

  useEffect(() => {
    if (!chatHydrated || (!submittedQuery && !chatMessages.length)) return;
    const save = () => {
      conversationSaveTimerRef.current = null;
      pendingConversationSaveRef.current = null;
      const snapshot = conversationSnapshot({ submittedQuery, products: [], searched: false, messages: chatMessages, resultsAfterMessageId: null, timeline });
      conversationSaveQueueRef.current = conversationSaveQueueRef.current
        .catch(() => undefined)
        .then(async () => {
          if (startFreshConversationRef.current) {
            await apiRequest<null>("/api/conversations/new", { method: "POST" });
            startFreshConversationRef.current = false;
          }
          const saved = await apiRequest<PersistedConversation>("/api/conversations/current", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(snapshot),
          });
          conversationIdRef.current = saved.id;
          setChatSaveFailed(false);
        })
        .catch((cause: unknown) => {
          setChatSaveFailed(true);
          setMessage(cause instanceof Error ? cause.message : "Could not save this chat.");
        });
    };
    const timer = window.setTimeout(save, 250);
    const flush = () => { window.clearTimeout(timer); save(); };
    conversationSaveTimerRef.current = timer;
    pendingConversationSaveRef.current = flush;
    return () => {
      window.clearTimeout(timer);
      if (conversationSaveTimerRef.current === timer) conversationSaveTimerRef.current = null;
      if (pendingConversationSaveRef.current === flush) pendingConversationSaveRef.current = null;
    };
  }, [apiRequest, chatHydrated, chatMessages, submittedQuery, timeline]);

  // The server reads chat context for voice sessions and typed turns; run any
  // debounced save now and wait for the queue.
  const flushConversationSave = useCallback(async () => {
    pendingConversationSaveRef.current?.();
    await conversationSaveQueueRef.current.catch(() => undefined);
  }, []);

  // While a drawer is open the page is frozen into a viewport-sized card (fixed, scrolled by
  // translateY) so the push transform never re-anchors the fixed header/composer to the
  // document; once closed the card has transform: none and the window scroll is restored.
  const drawer: DrawerName | null = chatDrawerOpen ? "chats" : accountSettingsOpen ? "settings" : null;
  const reducedMotion = useReducedMotion() ?? false;
  const [viewportWidth, setViewportWidth] = useState(0);
  const [insets, setInsets] = useState({ top: 0, bottom: 0 });
  const [push, setPush] = useState<{ side: DrawerName; scrollY: number } | null>(null);
  const pushRef = useRef<{ side: DrawerName; scrollY: number } | null>(null);
  const restoreScrollRef = useRef<number | null>(null);
  const openDrawer = (next: DrawerName) => {
    setChatDrawerOpen(next === "chats");
    setAccountSettingsOpen(next === "settings");
  };
  // Order help is a popup; the order card's "Need help?" and the `support.open` UI event open it.
  // "What do you know about me?" opens Settings on the memory page.
  const openMemorySummary = useCallback(() => {
    setSettingsSection("memory");
    setAccountSettingsOpen(true);
  }, []);
  const openSettingsSection = useCallback((section: SettingsSection) => {
    setChatDrawerOpen(false);
    setSettingsSection(section);
    setAccountSettingsOpen(true);
  }, []);
  const openDirections = useCallback((store: "swiggy" | "zepto") => {
    setChatDrawerOpen(false);
    setAccountSettingsOpen(false);
    setDirectionsStore(store);
  }, []);
  const openSupport = useCallback(() => {
    setChatDrawerOpen(false);
    setAccountSettingsOpen(false);
    setSupportOpen(true);
  }, []);
  const releasePush = useCallback(() => {
    if (!pushRef.current) return;
    restoreScrollRef.current = pushRef.current.scrollY;
    pushRef.current = null;
    setPush(null);
  }, []);

  useEffect(() => {
    const viewport = window.visualViewport;
    const update = () => {
      setViewportWidth(window.innerWidth);
      const next = viewport ? viewportInsets({ layoutHeight: window.innerHeight, visualHeight: viewport.height, visualOffsetTop: viewport.offsetTop, scale: viewport.scale }) : { top: 0, bottom: 0 };
      setInsets((current) => current.top === next.top && current.bottom === next.bottom ? current : next);
    };
    update();
    window.addEventListener("resize", update);
    viewport?.addEventListener("resize", update);
    viewport?.addEventListener("scroll", update);
    return () => {
      window.removeEventListener("resize", update);
      viewport?.removeEventListener("resize", update);
      viewport?.removeEventListener("scroll", update);
    };
  }, []);

  useLayoutEffect(() => {
    if (drawer) {
      if (pushRef.current?.side === drawer) return;
      scrollAnimatingRef.current = false;
      pushRef.current = { side: drawer, scrollY: pushRef.current?.scrollY ?? window.scrollY };
      setPush(pushRef.current);
    } else if (reducedMotion) {
      releasePush();
    }
  }, [drawer, reducedMotion, releasePush]);

  useLayoutEffect(() => {
    if (push || restoreScrollRef.current === null) return;
    const savedScrollY = restoreScrollRef.current;
    restoreScrollRef.current = null;
    window.scrollTo(0, restoreScrollTarget({ savedScrollY, follow: followScrollRef.current, scrollHeight: document.documentElement.scrollHeight, viewportHeight: window.innerHeight }));
  }, [push]);

  useEffect(() => {
    const dock = dockRef.current;
    const section = chatSectionRef.current;
    if (!dock || !section || typeof ResizeObserver === "undefined") return;
    let lastSectionHeight = section.offsetHeight;
    const observer = new ResizeObserver(() => {
      setDockHeight(dock.offsetHeight);
      const height = section.offsetHeight;
      // Content that grows after it was added (a card finishing its layout, images loading)
      // keeps the reader at the bottom while they are following the conversation.
      // A running auto-scroll aims at the live bottom every frame, so it already covers this;
      // otherwise jump straight there (an instant scroll keeps "following" true).
      if (height > lastSectionHeight && followScrollRef.current && !pushRef.current && !scrollAnimatingRef.current) {
        window.scrollTo(0, Math.max(0, document.documentElement.scrollHeight - window.innerHeight));
      }
      lastSectionHeight = height;
    });
    observer.observe(dock);
    observer.observe(section);
    setDockHeight(dock.offsetHeight);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const updateFollow = () => {
      if (scrollAnimatingRef.current || pushRef.current) return;
      followScrollRef.current = isNearBottom({ scrollTop: window.scrollY, viewportHeight: window.innerHeight, scrollHeight: document.documentElement.scrollHeight });
    };
    // A wheel, touch or key scroll by the reader stops any auto-scroll in progress.
    const yieldToReader = (event: Event) => {
      if (event.type === "keydown" && event.target instanceof HTMLElement && event.target.closest("input, textarea, [contenteditable]")) return;
      scrollAnimatingRef.current = false;
    };
    window.addEventListener("scroll", updateFollow, { passive: true });
    for (const name of ["wheel", "touchstart", "keydown"] as const) window.addEventListener(name, yieldToReader, { passive: true });
    return () => {
      window.removeEventListener("scroll", updateFollow);
      for (const name of ["wheel", "touchstart", "keydown"] as const) window.removeEventListener(name, yieldToReader);
    };
  }, []);

  useEffect(() => {
    if (!chatHydrated || (!submittedQuery && !chatMessages.length && !timeline.live.length && !voiceOpen && !trackingOpen)) return;
    if (!followScrollRef.current || pushRef.current) return;
    let frame = window.requestAnimationFrame(() => {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const destination = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      if (reducedMotion) {
        window.scrollTo(0, destination);
        return;
      }

      const origin = window.scrollY;
      const duration = 700;
      const startedAt = window.performance.now();
      scrollAnimatingRef.current = true;
      const animate = (now: number) => {
        if (!scrollAnimatingRef.current) return;
        const progress = Math.min(1, (now - startedAt) / duration);
        const eased = progress < 0.5
          ? 4 * progress * progress * progress
          : 1 - Math.pow(-2 * progress + 2, 3) / 2;
        // Re-read the bottom each frame: cards keep growing while the scroll runs.
        const liveDestination = Math.max(destination, document.documentElement.scrollHeight - window.innerHeight);
        window.scrollTo(0, origin + (liveDestination - origin) * eased);
        if (progress < 1) frame = window.requestAnimationFrame(animate);
        else scrollAnimatingRef.current = false;
      };
      frame = window.requestAnimationFrame(animate);
    });
    return () => {
      window.cancelAnimationFrame(frame);
      scrollAnimatingRef.current = false;
    };
  }, [chatHydrated, chatMessages, loading, submittedQuery, timeline, trackingOpen, voiceOpen]);

  async function startNewChat() {
    if (conversationSaveTimerRef.current !== null) {
      window.clearTimeout(conversationSaveTimerRef.current);
      conversationSaveTimerRef.current = null;
    }
    setVoiceOpen(false);
    setSubmittedQuery("");
    setChatMessages([]);
    chatMessagesRef.current = [];
    conversationIdRef.current = null;
    setChatFailure(null);
    setTimeline(emptyTimeline());
    billStageRef.current = {};
    pendingBillStageRef.current = {};
    setVoiceApproval(null);
    setOrderCard(null);
    setComparisonCard(null);
    swapRef.current = null;
    followScrollRef.current = true;
    setQuery("");
    try {
      await conversationSaveQueueRef.current;
      await apiRequest<null>("/api/conversations/new", { method: "POST" });
      setChatDrawerOpen(false);
      setRecentChats([]);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not start a new chat.");
    }
  }

  async function openSavedChat(conversationId: string) {
    if (conversationSaveTimerRef.current !== null) {
      window.clearTimeout(conversationSaveTimerRef.current);
      conversationSaveTimerRef.current = null;
    }
    try {
      await conversationSaveQueueRef.current;
      const conversation = await apiRequest<PersistedConversation>(`/api/conversations/${conversationId}/open`, { method: "POST" });
      setVoiceOpen(false);
      startFreshConversationRef.current = false;
      applyConversation(conversation);
      setQuery("");
      setChatDrawerOpen(false);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not open that chat.");
    }
  }

  async function selectAddress(nextAddressId: string) {
    const previous = addressId;
    setAddressId(nextAddressId);
    try {
      await apiRequest("/api/swiggy/addresses/selected", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address_id: nextAddressId }),
      });
    } catch (cause) {
      setAddressId(previous);
      setMessage(cause instanceof Error ? cause.message : "Could not save this address.");
    }
  }

  async function selectZeptoAddress(nextAddressId: string) {
    const previous = zeptoAddressId;
    setZeptoAddressId(nextAddressId);
    try {
      await apiRequest("/api/zepto/addresses/selected", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ address_id: nextAddressId }),
      });
      setZeptoSearchReady(true);
      setAddressRemovedToast(false);
    } catch (cause) {
      setZeptoAddressId(previous);
      setMessage(cause instanceof Error ? cause.message : "Could not save this Zepto address.");
    }
  }

  function applyAddedZeptoAddress(data: { addresses: Address[]; resolution: { default_address_id?: string | null } }) {
    setZeptoAddresses(data.addresses);
    const selected = data.resolution.default_address_id;
    if (selected && data.addresses.some((address) => address.id === selected)) {
      setZeptoAddressId(selected);
    }
    setZeptoSearchReady(data.addresses.length > 0);
    closeAddressPopup();
    setMessage("Address saved to Zepto and refreshed here.");
  }

  async function runSearch(value: string) {
    const clean = value.trim();
    const attachmentId = attachmentIdFor(photo.state);
    if (!clean && !attachmentId) return;
    if (photo.state.status === "uploading") return;
    const localReply = attachmentId ? null : conversationReply(clean, { swiggyConnected: connected === true, zeptoConnected: zeptoConnected === true });
    if (localReply) {
      followScrollRef.current = true;
      const now = new Date().toISOString();
      setChatMessages((current) => {
        const next = [
          ...current,
        { id: `text-${Date.now().toString(36)}-user`, role: "user", content: clean, modality: "text", created_at: now },
        { id: `text-${Date.now().toString(36)}-assistant`, role: "assistant", content: localReply, modality: "text", created_at: now },
        ] as ChatMessage[];
        chatMessagesRef.current = next;
        return next;
      });
      setQuery("");
      setVoiceOpen(false);
      setMessage("Ready when you are.");
      return;
    }
    if (loading === "assistant") return;
    const swiggyReady = connected === true && Boolean(addressId);
    const zeptoReady = zeptoConnected === true && Boolean(zeptoAddressId);
    followScrollRef.current = true;
    // The full photo is never saved with the chat: the turn keeps a "Photo" placeholder and
    // the server-made small thumbnail; the sharper local preview lives only in this session.
    const photoFields = attachmentId && photo.state.status === "ready" ? { photo_thumbnail: photo.state.thumbnail, photo_preview: photo.takePreview() } : {};
    const userMessage: ChatMessage = { id: `text-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`, role: "user", content: attachmentId ? photoMessageContent(clean) : clean, modality: "text", created_at: new Date().toISOString(), ...photoFields };
    const withUser = [...chatMessagesRef.current, userMessage];
    chatMessagesRef.current = withUser;
    setChatMessages(withUser);
    setQuery("");
    setVoiceOpen(false);
    setChatFailure(null);
    setLoading("assistant");
    try {
      await flushConversationSave();
      const turn = await apiRequest<ChatTurnResponse>("/api/chat/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: conversationIdRef.current, message: clean, attachment_id: attachmentId, swiggy_address_id: swiggyReady ? addressId : null, zepto_address_id: zeptoReady ? zeptoAddressId : null, ui_acks: pendingTypedAcksRef.current.splice(0, 4) }),
      });
      if (turn.conversation_id) conversationIdRef.current ??= turn.conversation_id;
      if (attachmentId) photo.consumed(attachmentId);
      const reply: ChatMessage = { id: `text-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}-assistant`, role: "assistant", content: turn.reply, modality: "text", created_at: new Date().toISOString() };
      const withReply = [...chatMessagesRef.current, reply];
      chatMessagesRef.current = withReply;
      setChatMessages(withReply);
      for (const event of turn.ui_events) {
        const outcome = applyAssistantEvent<Product>(event, { onResults: handleVoiceResults, onBasketTouched: handleBasketTouched, onApprovalRequested: handleVoiceApproval, onAddressForm: handleVoiceAddressForm, onAddressSelected: handleAddressSelected, onAddressRemoved: handleAddressRemoved, onOrderConfirmation: handleOrderConfirmation, onSupportOpen: openSupport, onPhotoRead: handlePhotoRead, onMemorySummary: openMemorySummary, onComparison: handleComparison });
        if (outcome?.kind === "not_connected") setSetupOpen(true);
        if (outcome?.ack && outcome.ackId) void outcome.ack.then((ack) => { pendingTypedAcksRef.current.push(ack); });
        if (outcome) setMessage(outcome.message);
      }
    } catch (cause) {
      const detail = cause instanceof ApiError && [502, 503, 504].includes(cause.status) ? cause.message : "GharSaathi couldn't reply just now.";
      setChatFailure({ query: clean, message: `${detail} You can search the stores directly instead.` });
    } finally {
      setLoading(null);
    }
  }

  // Fallback when the assistant turn fails: one direct store search for the
  // message the user already sent, shown as its own result group.
  async function runDirectSearch(clean: string) {
    const searchKey = `${addressId}\u0000${zeptoAddressId}\u0000${clean.toLocaleLowerCase()}`;
    const inFlight = searchRequestsRef.current.get(searchKey);
    if (inFlight) return inFlight;
    const now = Date.now();
    const lastSearch = lastSearchRef.current;
    if (lastSearch?.key === searchKey && now - lastSearch.at < 800) return;
    lastSearchRef.current = { key: searchKey, at: now };

    followScrollRef.current = true;
    setSubmittedQuery(clean);
    const afterMessageId = latestMessageId(chatMessagesRef.current);
    const swiggyReady = connected === true && Boolean(addressId);
    const zeptoReady = zeptoConnected === true && Boolean(zeptoAddressId);
    if (!swiggyReady && !zeptoReady) {
      setSetupOpen(true);
      setMessage("Connect a store and choose its saved delivery address first.");
      return;
    }
    const identity = resultGroupIdentity({ query: clean });
    setTimeline((current) => recordResultGroup(current, { ...identity, query: clean, products: [], status: "searching", afterMessageId }));

    const request = (async () => {
      setLoading("search");
      setMessage(commerceMode === "demo" ? "Searching both static demo catalogues…" : "Checking each ready store once…");
      try {
        const searches: Promise<Product[]>[] = [];
        if (swiggyReady) searches.push(
          apiRequest<{ products: Product[] }>("/api/swiggy/search/personalized", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: clean, address_id: addressId, offset: 0 }) })
            .then((data) => data.products.map((product) => ({ ...product, provider: "swiggy_instamart" as const, provider_label: "Swiggy Instamart" }))),
        );
        if (zeptoReady) searches.push(
          apiRequest<{ products: Product[] }>("/api/zepto/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: clean, address_id: zeptoAddressId, page_number: 0 }) })
            .then((data) => data.products.map((product) => ({ ...product, provider: "zepto" as const, provider_label: "Zepto" }))),
        );
        const outcome = directSearchOutcome(await Promise.allSettled(searches));
        if (outcome.status === "failed") {
          // A store error is not "no results": show the error with its retry, not an empty group.
          setTimeline((current) => dropResultGroup(current, identity.key));
          setChatFailure({ query: clean, message: outcome.message });
          setMessage(outcome.message);
        } else {
          const combined = outcome.status === "results" ? outcome.products : [];
          setTimeline((current) => finishResultGroup(current, identity.key, { products: combined, status: "done" }));
          if (combined.length) setMessage(`${combined.length} ${commerceMode === "demo" ? "demo" : "live"} options found across ready stores.`);
          else setMessage("No matching products found at the selected addresses.");
        }
      } catch (cause) {
        setTimeline((current) => dropResultGroup(current, identity.key));
        setMessage(cause instanceof Error ? cause.message : "Search failed.");
      } finally {
        setLoading(null);
      }
    })();
    searchRequestsRef.current.set(searchKey, request);
    void request.finally(() => {
      if (searchRequestsRef.current.get(searchKey) === request) searchRequestsRef.current.delete(searchKey);
    });
    return request;
  }

  async function addProduct(product: Product) {
    if (!product.available || !product.spin_id) return false;
    const provider = product.provider ?? "swiggy_instamart";
    setAddingSku(product.sku_id);
    try {
      const next = await addBasketLine(provider, {
        ref: { provider, product_id: product.product_id, variant_id: product.spin_id, sku_id: product.sku_id },
        name: product.name,
        brand: product.brand,
        pack_size: product.pack_size,
        unit_price_paise: Math.round(product.price.offer_price * 100),
        mrp_paise: Math.round(product.price.mrp * 100),
        quantity: 1,
        available: product.available,
      });
      showBasketAtLatest(provider);
      handleBasketChange(next);
      setMessage(`${product.name} added to basket.`);
      const swap = swapRef.current;
      if (swap && timeline.groups.find((group) => group.key === swap.groupKey)?.products.some((option) => (option as Product).spin_id === product.spin_id)) {
        swapRef.current = null;
        if (swap.provider !== provider || swap.variantId !== product.spin_id) {
          const after = await setBasketLineQuantity(swap.provider, swap.variantId, 0).catch(() => null);
          if (after) { handleBasketChange(after); window.dispatchEvent(new Event(BASKET_CHANGED_EVENT)); }
          appendMessage("assistant", after ? `Swapped ${swap.name} for ${product.name}.` : `${product.name} added. ${swap.name} is still in your basket—remove it there if you don't need it.`);
        }
      }
      return true;
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not add this item.");
      return false;
    } finally {
      setAddingSku(null);
    }
  }

  // The option card's − / + use the same basket line endpoint as the basket card.
  async function changeProductQuantity(product: Product, quantity: number) {
    if (!product.spin_id) return false;
    const provider = product.provider ?? "swiggy_instamart";
    try {
      const next = await setBasketLineQuantity(provider, product.spin_id, quantity);
      handleBasketChange(next);
      window.dispatchEvent(new Event(BASKET_CHANGED_EVENT));
      setMessage(quantity > 0 ? `${product.name}: ${quantity} in basket.` : `${product.name} removed from basket.`);
      return true;
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not update the quantity.");
      return false;
    }
  }

  async function disconnect() {
    setLoading("disconnect");
    try {
      await apiRequest<null>("/api/swiggy/disconnect", { method: "POST" });
      swiggyConnectionRequest = null;
      setConnected(false); setAddresses([]); setAddressId(""); setTimeline((current) => ({ ...current, groups: [] })); setMessage("Swiggy disconnected.");
      return true;
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : "Could not disconnect Swiggy."); return false; }
    finally { setLoading(null); }
  }

  async function disconnectZepto() {
    setZeptoBusy(true);
    try {
      await apiRequest<null>("/api/zepto/disconnect", { method: "POST" });
      zeptoConnectionRequest = null;
      setZeptoConnected(false);
      setZeptoSearchReady(false);
      setZeptoAddresses([]);
      setZeptoAddressId("");
      return true;
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not disconnect Zepto.");
      return false;
    } finally {
      setZeptoBusy(false);
    }
  }

  /** Checks the OAuth callback first, then opens the store's own login. Returns a problem to show, or null. */
  async function beginProviderConnection(provider: "swiggy" | "zepto"): Promise<string | null> {
    if (provider === "zepto") setZeptoBusy(true);
    else setLoading("connection");
    try {
      const readiness = await apiRequest<{ ready: boolean; message: string }>(`/api/${provider}/oauth/readiness`);
      if (!readiness.ready) {
        setMessage(readiness.message);
        return readiness.message;
      }
      const { authorization_url: authorizationUrl } = await apiRequest<{ authorization_url: string }>(`/api/${provider}/oauth/authorization`, { method: "POST" });
      window.location.assign(authorizationUrl);
      return null;
    } catch (cause) {
      const problem = cause instanceof Error ? cause.message : `Could not prepare ${provider} login.`;
      setMessage(problem);
      return problem;
    } finally {
      if (provider === "zepto") setZeptoBusy(false);
      else setLoading((value) => value === "connection" ? null : value);
    }
  }

  const basketCount = cartBadgeCount(baskets);
  const storeItemCount = (provider: ProviderName) => cartBadgeCount({ [provider]: baskets[provider] });
  // The header cart opens a store that actually has items, so the badge and the basket it opens always agree.
  const cartProvider: ProviderName = storeItemCount(basketProvider) > 0
    ? basketProvider
    : (["zepto", "swiggy_instamart"] as ProviderName[]).find((provider) => storeItemCount(provider) > 0) ?? basketProvider;
  const hasChat = Boolean(submittedQuery || chatMessages.length);
  // Swiggy-style address block in the header, and "Where should we deliver?" before the greeting.
  const storeAddresses = {
    zepto: { connected: zeptoConnected, addresses: zeptoAddresses, selectedId: zeptoAddressId, loaded: zeptoAddressesLoaded },
    swiggy: { connected, addresses, selectedId: addressId, loaded: swiggyAddressesLoaded },
  };
  const headerAddressView = headerAddress(storeAddresses);
  const deliverGate = hasChat ? null : addressGate(storeAddresses);
  const openHeaderAddress = () => {
    if (headerAddressView.store) setAddressPicker(headerAddressView.store);
    else if (headerAddressView.state === "connect") openSettingsSection("stores");
  };
  const chips = quick.chips(orderChip !== null);
  const basketStores = (["zepto", "swiggy_instamart"] as ProviderName[]).filter((provider) => storeItemCount(provider) > 0);
  const chipsLocked = loading === "search" || loading === "assistant";
  const replyLanguage = String((user?.unsafeMetadata.gharsaathiProfile as { language?: string } | undefined)?.language ?? "hinglish");
  const filteredRecentChats = searchConversations(recentChats, chatSearch);
  const disconnectText = disconnectCopy(disconnectConfirm.store);
  const newestGroupSeq = Math.max(0, ...timeline.groups.map((group) => group.seq));
  const renderResultGroup = (group: ResultGroup<Product>) => {
    if (group.status === "searching") return <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 sm:mr-12"><div className="min-w-0 rounded-[8px_22px_22px_22px] border border-border bg-surface px-5 py-4"><div className="flex items-center gap-1.5" aria-label={`GharSaathi is searching for ${group.label}`}>{[0,1,2].map((dot) => <motion.i key={dot} animate={{ y: [0,-4,0] }} transition={{ duration: .8, repeat: Infinity, delay: dot * .13 }} className="size-1.5 rounded-full bg-accent" />)}<span className="ml-2 text-sm text-muted">{commerceMode === "demo" ? "Searching the demo catalogue" : zeptoSearchReady ? "Searching Swiggy and Zepto" : "Searching Swiggy Instamart"}</span></div></div></motion.div>;
    if (!group.products.length) return <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 sm:mr-12"><div className="min-w-0 max-w-md rounded-[8px_22px_22px_22px] border border-border bg-surface px-5 py-4"><div className="flex gap-3"><Basket size={20} className="mt-0.5 shrink-0 text-accent-ink" /><p className="text-sm leading-6"><strong className="font-semibold">{group.label}:</strong> nothing found at this address. Try a broader product or brand name.</p></div></div></motion.div>;
    return <div className="mt-8 min-w-0"><ProductOptions label={group.label} products={group.products} commerceMode={commerceMode} addingSku={addingSku} baskets={baskets} onAdd={addProduct} onQuantity={changeProductQuantity} />{group.seq === newestGroupSeq && <p className="mt-2 flex items-start gap-2 px-1 text-[10px] leading-4 text-muted"><ShieldCheck size={14} className="mt-0.5 shrink-0" /> Each store keeps its own basket and exact bill. Nothing is purchased without your approval.</p>}</div>;
  };

  return <main className="grain min-h-[100dvh] overflow-hidden bg-surface-muted text-ink">
    <AnimatePresence>
      {chatDrawerOpen && <>
        <motion.aside initial={reducedMotion ? { opacity: 0 } : { x: "-100%" }} animate={{ x: 0, opacity: 1 }} exit={reducedMotion ? { opacity: 0 } : { x: "-100%" }} transition={DRAWER_PANEL_TRANSITION} aria-label="Shopping chats" className="fixed inset-y-0 left-0 z-[35] flex w-3/4 max-w-[360px] flex-col bg-surface-muted">
          <div className="flex items-center gap-2 px-3 pb-3 pt-[max(1rem,env(safe-area-inset-top))]">
            <Brand dense />
            <button type="button" onClick={() => setChatDrawerOpen(false)} aria-label="Close chats" className="ml-auto grid size-9 shrink-0 place-items-center rounded-full text-muted transition hover:bg-surface-strong active:scale-[.96]"><X size={18} /></button>
          </div>
          <div className="px-3 pb-5 pt-2">
            <label className="flex h-11 items-center gap-2 rounded-2xl border border-border-strong bg-white px-3 text-muted transition focus-within:border-accent focus-within:ring-2 focus-within:ring-warning-border">
              <MagnifyingGlass size={18} />
              <input value={chatSearch} onChange={(event) => setChatSearch(event.target.value)} placeholder="Search chats" className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-subtle" />
              {chatSearch && <button type="button" onClick={() => setChatSearch("")} aria-label="Clear chat search" className="grid size-7 place-items-center rounded-lg text-subtle hover:bg-surface-strong"><X size={14} /></button>}
            </label>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-5">
            <div className="flex items-center justify-between">
              <p className="text-[11px] font-bold uppercase tracking-[.16em] text-subtle">Recent chats</p>
              {!recentChatsLoading && recentChats.length > 0 && <span className="text-[11px] font-medium text-subtle">{recentChats.length}</span>}
            </div>
            <div className="mt-3 space-y-1">
              {recentChatsLoading && <div className="space-y-2" aria-label="Loading chats">{[0, 1, 2].map((item) => <div key={item} className="animate-pulse border-b border-border px-1 py-4"><span className="block h-3 w-2/3 rounded bg-border" /><span className="mt-2 block h-2.5 w-5/6 rounded bg-surface-strong" /></div>)}</div>}
              {!recentChatsLoading && !filteredRecentChats.length && <div className="flex flex-col items-center px-2 pb-8 pt-14 text-center">
                <span className="grid size-12 place-items-center rounded-2xl bg-surface-strong text-accent-strong"><ChatCircleDots size={22} weight="duotone" /></span>
                <p className="mt-4 text-sm font-semibold leading-5 text-ink">{chatSearch.trim() ? "No matching chats" : "No shopping chats yet"}</p>
                <p className="mt-2 max-w-[24ch] text-[13px] leading-5 text-muted">{chatSearch.trim() ? "Try a different search." : "Say what you need to start. Your chats are saved here."}</p>
              </div>}
              {!recentChatsLoading && filteredRecentChats.map((chat) => <button type="button" key={chat.id} onClick={() => void openSavedChat(chat.id)} className={`group relative flex w-full items-start gap-3 rounded-2xl px-3.5 py-3.5 text-left transition hover:bg-surface-strong ${chat.is_active ? "bg-surface ring-1 ring-border" : ""}`}>
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline gap-2">
                    <span className="block min-w-0 flex-1 truncate text-sm font-semibold text-ink">{conversationTitle(chat)}</span>
                    <span className="shrink-0 text-[11px] text-subtle">{relativeChatDate(chat.updated_at)}</span>
                  </span>
                  <span className="mt-1 block truncate text-xs leading-4 text-muted">{chat.preview}</span>
                </span>
              </button>)}
            </div>
          </div>
          <div className="bg-surface-muted px-3 pb-[max(3.5625rem,env(safe-area-inset-bottom))] pt-4">
            <div className="flex items-center gap-2">
              <button type="button" onClick={() => void startNewChat()} className="flex h-14 min-w-0 flex-1 items-center justify-center gap-2 rounded-[22px] bg-accent px-4 text-sm font-semibold text-on-accent transition hover:bg-accent active:scale-[.97]"><NotePencil size={19} weight="bold" className="shrink-0" /> New chat</button>
              <span aria-label={`${userName} profile`} title={userName} className="grid size-14 shrink-0 place-items-center rounded-full bg-border text-[15px] font-semibold uppercase text-accent-strong ring-1 ring-accent-border">
                {userName.trim().charAt(0) || "G"}
              </span>
            </div>
          </div>
        </motion.aside>
      </>}
    </AnimatePresence>
    <motion.div animate={{ x: pushOffset(drawer, viewportWidth, reducedMotion) }} transition={DRAWER_SPRING} transformTemplate={(_, generated) => push ? generated : "none"} onAnimationComplete={() => { if (!drawer) releasePush(); }} className={`${push ? "fixed inset-0 overflow-hidden" : "relative min-h-[100dvh]"} z-30 bg-surface-muted transition-[border-radius] duration-150 ease-out ${push && !reducedMotion ? `${drawer ? (push.side === "chats" ? "rounded-l-[36px]" : "rounded-r-[36px]") : "rounded-none"} [corner-shape:squircle]` : ""}`}>
    <AnimatePresence>
      {drawer && <motion.button type="button" aria-label={drawer === "chats" ? "Close chats" : "Close account settings"} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.12, ease: "easeOut" }} onClick={() => { setChatDrawerOpen(false); setAccountSettingsOpen(false); }} className="fixed inset-0 z-40 bg-scrim/20" />}
    </AnimatePresence>
    <HomeHeader
      top={insets.top}
      address={headerAddressView}
      basketCount={basketCount}
      orderChip={orderChip}
      userName={userName}
      userImageUrl={user?.hasImage ? user.imageUrl : undefined}
      accountOpen={accountSettingsOpen}
      onOpenChats={() => openDrawer("chats")}
      onOpenAddress={openHeaderAddress}
      onOpenBasket={() => setCartOpen((open) => !open)}
      onOpenAccount={() => openDrawer("settings")}
      onOpenOrder={(orderId) => document.getElementById(`order-status-${orderId}`)?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" })}
    />

    <section ref={chatSectionRef} style={{ ...(push ? { transform: `translateY(${-push.scrollY}px)` } : {}), ...(dockHeight ? { paddingBottom: dockHeight + 24 } : {}) }} className={`mx-auto flex min-h-[100dvh] max-w-4xl flex-col overflow-x-clip px-4 ${orderChip ? "pt-[calc(148px+env(safe-area-inset-top))]" : "pt-[calc(96px+env(safe-area-inset-top))]"} sm:px-6 ${chips.length && !voiceOpen ? "pb-52 sm:pb-56" : "pb-40 sm:pb-44"}`}>
      <div className="flex-1">
        <div className="mb-5 sm:mb-8"><span className="text-[10px] font-semibold uppercase tracking-[.18em] text-subtle">Today</span></div>
        {deliverGate ? <motion.div key="deliver-first" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} data-testid="deliver-first">
          <DeliverFirst
            gate={deliverGate}
            commerceMode={commerceMode}
            refreshing={deliverGate.store === "zepto" ? zeptoBusy : loading === "addresses"}
            onSelect={(id) => void (deliverGate.store === "zepto" ? selectZeptoAddress(id) : selectAddress(id))}
            onAdd={deliverGate.canAdd ? () => setAddressPopupOpen(true) : undefined}
            onRefresh={() => void (deliverGate.store === "zepto" ? loadZeptoAddresses(true) : loadAddresses(true))}
          />
        </motion.div> : <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mr-7 sm:mr-12">
          <div className="min-w-0 max-w-[620px]">
            <div className="w-fit max-w-full rounded-[7px_20px_20px_20px] border border-border bg-surface px-4 py-3.5 sm:px-5 sm:py-4">{replyLanguage === "english"
              ? <><p className="text-[15px] leading-6 sm:text-base sm:leading-7">Hi {userName}, <strong className="font-semibold">what do you need for home today?</strong></p><p className="mt-1 text-[13px] leading-5 text-muted sm:mt-1.5 sm:text-sm sm:leading-6">Just say it—I&apos;ll check all your ready stores together.</p></>
              : <><p className="text-[13px] leading-5 text-muted">Hello, {userName} 👋</p><p className="mt-1 text-[17px] font-semibold leading-6 text-ink sm:text-lg">Aaj ghar ke liye kya mangwana hai?</p><p className="mt-1.5 text-[13px] leading-5 text-muted sm:text-sm sm:leading-6">Bas bol dijiye—main ready stores par saath mein check karungi.</p></>}</div>
            {!hasChat && <div className="mt-3 flex flex-wrap gap-2">{suggestions.map((suggestion) => <button key={suggestion.query} onClick={() => void runSearch(suggestion.query)} disabled={loading === "search" || loading === "assistant"} className="min-h-10 whitespace-nowrap rounded-full border border-border bg-surface px-4 py-2 text-[13px] font-medium leading-5 text-ink-secondary transition hover:bg-surface-strong active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45 sm:min-h-0 sm:rounded-full sm:px-3.5 sm:text-xs">{suggestion.label}</button>)}</div>}
          </div>
        </motion.div>}

        <AnimatePresence initial={false}>
          {placeInboundCalls(placeRiderCalls(withComparisonCard(withOrderCard(placeTimelineItems({ messages: chatMessages, timeline }), orderCard), comparisonCard), riderCalls.cards), inboundCalls.cards).map((item) => {
            if (item.kind === "comparison") return <ComparisonCard key={item.card.comparison.id} comparison={item.card.comparison} commerceMode={commerceMode} addingSku={addingSku} baskets={baskets} onAdd={addProduct} onHide={() => setComparisonCard(null)} />;
            if (item.kind === "inbound-call") return <InboundCallCard key={`inbound-call-${item.card.id}`} call={item.card} onDismiss={inboundCalls.dismiss} />;
            if (item.kind === "rider-call") return <RiderCallCard key={`rider-call-${item.card.id}`} call={item.card} onDismiss={riderCalls.dismiss} />;
            if (item.kind === "order-card") {
              const { card } = item;
              if (card.superseded) return <motion.div key={orderCardKey(card)} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-6 mr-8 flex min-w-0 sm:mr-12"><p role="status" className="flex max-w-full items-center gap-2 truncate rounded-full bg-surface-strong px-3 py-1.5 text-[11px] font-medium text-muted"><ShoppingCartSimple size={13} className="shrink-0 text-subtle" /><span className="truncate">{supersededOrderText(card)}</span></p></motion.div>;
              return <OrderConfirmationCard key={orderCardKey(card)} confirmation={card.confirmation} onResult={(state) => handleOrderCardResult(card, state)} />;
            }
            if (item.kind === "order-status") return <OrderStatusCard key={`order-status-${item.card.orderId}`} card={item.card} onChange={handleOrderStatusChange} onUpdate={setOrderToast} riderLine={riderDirectionsLine(inboundCalls.calls, item.card)} deliveryLineReady={deliveryLine?.enabled === true} onOpenDeliveryLine={() => openSettingsSection("delivery")} onOpenDirections={() => openDirections("zepto")} onOpenSupport={openSupport} />;
            if (item.kind === "results") return item.group ? <div key={`results-${item.group.key}`} className="contents">{renderResultGroup(item.group as ResultGroup<Product>)}</div> : null;
            if (item.kind === "basket-summary") {
              const { card } = item;
              const linked = card.stage === "ordered" && card.orderId ? card.orderId : null;
              const chip = <><ShoppingCartSimple size={13} className="shrink-0 text-subtle" /><span className="shrink-0 font-semibold text-ink-secondary">{card.provider === "zepto" ? "Zepto" : "Instamart"}</span><span className="truncate">{summaryText(card)}</span></>;
              return <motion.div key={`basket-summary-${card.provider}-${card.seq}`} initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-6 mr-8 flex min-w-0 sm:mr-12">{linked
                ? <button type="button" onClick={() => document.getElementById(`order-status-${linked}`)?.scrollIntoView({ behavior: "smooth", block: "center" })} aria-label={`${summaryText(card)}. Show the order status`} className="flex max-w-full items-center gap-2 truncate rounded-full bg-surface-strong px-3 py-1.5 text-[11px] font-medium text-muted transition hover:bg-border-strong">{chip}</button>
                : <p className="flex max-w-full items-center gap-2 truncate rounded-full bg-surface-strong px-3 py-1.5 text-[11px] font-medium text-muted">{chip}</p>}</motion.div>;
            }
            if (item.kind === "basket") {
              const { provider } = item.card;
              return <motion.div key={`basket-${provider}`} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} className="mt-8 mr-8 min-w-0 sm:mr-12"><BasketPanel provider={provider} addressId={provider === "zepto" ? zeptoAddressId : addressId} commerceMode={commerceMode} incomingRequest={voiceApproval?.provider === provider ? voiceApproval.request : null} onBasketChange={handleBasketChange} onApprovalChange={handleApprovalChange} onBillStep={handleBillStep} onSwap={(line) => startSwap(provider, line)} onSaveAsList={() => quick.openLists("save-basket")} /><button type="button" onClick={() => hideBasket(provider)} className="mx-auto mt-3 block text-xs font-semibold text-muted transition hover:text-ink">Hide basket</button></motion.div>;
            }
            const chatMessage = item.message;
            return chatMessage.role === "user" ? (
              <motion.div key={chatMessage.id} initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="mt-8 flex justify-end pl-8 sm:pl-12">
                {chatMessage.photo_thumbnail || chatMessage.photo_preview ? (
                  <div className="flex max-w-full flex-col items-end gap-1.5">
                    <PhotoBubbleImage thumbnail={chatMessage.photo_thumbnail} preview={chatMessage.photo_preview} />
                    {photoBubbleText(chatMessage.content) && <div className="max-w-full whitespace-pre-wrap [overflow-wrap:anywhere] rounded-[22px_8px_22px_22px] border border-accent-border bg-user-bubble px-5 py-3.5 text-[15px] leading-6 text-on-user-bubble">{photoBubbleText(chatMessage.content)}</div>}
                  </div>
                ) : <div className="max-w-full whitespace-pre-wrap [overflow-wrap:anywhere] rounded-[22px_8px_22px_22px] border border-accent-border bg-user-bubble px-5 py-3.5 text-[15px] leading-6 text-on-user-bubble">{chatMessage.content}</div>}
              </motion.div>
            ) : (
              <motion.div key={chatMessage.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 min-w-0 sm:mr-12">
                <div className="w-fit max-w-[620px] whitespace-pre-wrap [overflow-wrap:anywhere] rounded-[8px_22px_22px_22px] border border-border bg-surface px-5 py-4 text-[15px] leading-7 sm:text-base">{chatMessage.content}</div>
              </motion.div>
            );
          })}
        </AnimatePresence>
        {loading === "assistant" && <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 sm:mr-12"><div className="w-fit rounded-[8px_22px_22px_22px] border border-border bg-surface px-5 py-4"><div className="flex items-center gap-1.5" role="status" aria-label="GharSaathi is typing">{[0,1,2].map((dot) => <motion.i key={dot} animate={{ y: [0,-4,0] }} transition={{ duration: .8, repeat: Infinity, delay: dot * .13 }} className="size-1.5 rounded-full bg-accent" />)}<span className="ml-2 text-sm text-muted">Thinking…</span></div></div></motion.div>}
        {chatFailure && loading !== "assistant" && <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 sm:mr-12"><div role="alert" className="max-w-md rounded-[8px_22px_22px_22px] border border-border bg-surface px-5 py-4"><p className="text-sm leading-6 text-danger-hover">{chatFailure.message}</p>{chatFailure.query && <button type="button" onClick={() => { const failedQuery = chatFailure.query; setChatFailure(null); void runDirectSearch(failedQuery); }} className="mt-3 flex items-center gap-1.5 rounded-full bg-inverse px-4 py-2 text-xs font-semibold text-on-inverse transition active:scale-[.97]"><MagnifyingGlass size={14} /> Search directly</button>}</div></motion.div>}
        {chatSaveFailed && <p role="status" className="mt-6 text-center text-[11px] leading-4 text-danger-hover">This chat hasn&apos;t been saved yet. GharSaathi will try again with your next message.</p>}

        <AnimatePresence>{trackingOpen && <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} className="mt-8 mr-8 min-w-0 sm:mr-12"><LiveOrderTracking initialAddressId={addressId || undefined} recipientName={userName} deliveryAddresses={{ swiggy: selectedAddress ? { id: selectedAddress.id, label: selectedAddress.tag || selectedAddress.category, text: selectedAddress.address_line } : undefined, zepto: selectedZeptoAddress ? { id: selectedZeptoAddress.id, label: selectedZeptoAddress.tag || selectedZeptoAddress.category, text: selectedZeptoAddress.address_line } : undefined }} onChangeAddress={setAddressPicker} onClose={() => setTrackingOpen(false)} /></motion.div>}</AnimatePresence>
      </div>
    </section>

    <AnimatePresence>{addressRemovedToast && !orderToast && addressPicker !== "zepto" && <motion.div key="address-removed-toast" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} style={{ bottom: insets.bottom + 112 }} className="pointer-events-none fixed inset-x-0 z-30 flex justify-center px-4"><button type="button" onClick={() => { setAddressRemovedToast(false); setAddressPicker("zepto"); }} className="pointer-events-auto max-w-sm rounded-full bg-inverse px-4 py-2.5 text-center text-sm font-medium text-on-inverse shadow-[0_18px_40px_-20px_rgb(var(--shadow)/.8)]">{ADDRESS_REMOVED_MESSAGE}</button></motion.div>}</AnimatePresence>
    <AnimatePresence>{notificationPrompt && !orderToast && <motion.div key="notification-prompt" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} style={{ bottom: insets.bottom + 112 }} className="pointer-events-none fixed inset-x-0 z-30 flex justify-center px-4"><OrderNotificationPrompt controller={notifications} onDone={() => setNotificationPrompt(false)} /></motion.div>}</AnimatePresence>
    <AnimatePresence>{quick.toast && !orderToast && <motion.div key={`quick-toast-${quick.toast.id}`} initial={{ opacity: 0, y: reducedMotion ? 0 : 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: reducedMotion ? 0 : 6 }} transition={{ duration: .18, ease: [0.23, 1, 0.32, 1] }} style={{ bottom: insets.bottom + (hasChat && chips.length && !voiceOpen ? 156 : 112) }} className="pointer-events-none fixed inset-x-0 z-30 flex justify-center px-4"><QuickToastView toast={quick.toast} onDismiss={quick.dismissToast} /></motion.div>}</AnimatePresence>
    <AnimatePresence>{orderToast && <motion.div key="order-toast" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} style={{ bottom: insets.bottom + 112 }} className="pointer-events-none fixed inset-x-0 z-30 flex justify-center px-4"><p role="status" aria-live="polite" className="pointer-events-auto max-w-sm rounded-full bg-inverse px-4 py-2.5 text-center text-sm font-medium text-on-inverse shadow-[0_18px_40px_-20px_rgb(var(--shadow)/.8)]">{orderToast}</p></motion.div>}</AnimatePresence>

    <div ref={dockRef} style={{ bottom: insets.bottom }} className={`fixed inset-x-0 bottom-0 z-20 ${deliverGate && !voiceOpen ? "hidden" : ""} bg-gradient-to-t from-surface-muted via-surface-muted to-transparent px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-10 sm:pb-[max(1.5rem,env(safe-area-inset-bottom))]`}>
      <AnimatePresence mode="wait">
        {voiceOpen ? <motion.div key="voice-composer" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: .16 }} className="mx-auto max-w-3xl"><PhotoChip state={photo.state} onRemove={photo.remove} voice /><div className="flex items-end justify-center gap-2"><PhotoAttachButton compact onFile={(file) => void photo.attach(file)} disabled={photo.state.status === "uploading"} /><VoicePanel addressId={addressId} zeptoAddressId={zeptoAddressId} onClose={closeVoice} onResults={handleVoiceResults} onTranscriptTurn={handleVoiceTranscript} onBasketTouched={handleBasketTouched} onApprovalRequested={handleVoiceApproval} onAddressForm={handleVoiceAddressForm} onAddressSelected={handleAddressSelected} onAddressRemoved={handleAddressRemoved} onOrderConfirmation={handleOrderConfirmation} onSupportOpen={openSupport} onPhotoRead={handlePhotoRead} onMemorySummary={openMemorySummary} onComparison={handleComparison} beforeSessionStart={flushConversationSave} /><span aria-hidden="true" className="size-11 shrink-0" /></div></motion.div> : <motion.form key="text-composer" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} onSubmit={(event: FormEvent) => { event.preventDefault(); void runSearch(query); }} className="mx-auto max-w-3xl">
          {chips.length > 0 && <QuickChips chips={chips} busyKey={quick.busyKey} disabled={chipsLocked} onChip={(chip) => { followScrollRef.current = true; void quick.run(chip); }} className="-mx-1 mb-2" />}
          <PhotoChip state={photo.state} onRemove={photo.remove} />
          <div className="flex items-center gap-1 rounded-[26px] border border-border-strong bg-surface p-2 pl-2 shadow-[0_24px_60px_-28px_rgb(var(--shadow)/.55),inset_0_1px_0_rgba(255,255,255,.9)]"><PhotoAttachButton onFile={(file) => void photo.attach(file)} disabled={loading === "search" || loading === "assistant" || photo.state.status === "uploading"} /><input value={query} onChange={(event) => setQuery(event.target.value)} disabled={loading === "search" || loading === "assistant"} className="min-w-0 flex-1 bg-transparent py-2 pl-1 text-[15px] outline-none placeholder:text-subtle disabled:cursor-not-allowed disabled:opacity-60" placeholder="Type or just tap the mic…" aria-label="Shopping request" />{query.trim() || photo.state.status !== "idle" ? <button type="submit" aria-label={attachmentIdFor(photo.state) ? "Send photo" : "Send request"} disabled={loading === "search" || loading === "assistant" || !canSend(photo.state, query)} className="grid size-12 shrink-0 place-items-center rounded-full bg-inverse text-on-inverse transition active:scale-[.96] disabled:cursor-not-allowed disabled:opacity-45"><PaperPlaneRight size={20} weight="fill" /></button> : <button type="button" onClick={() => setVoiceOpen(true)} onPointerEnter={prewarmVoice} onPointerDown={prewarmVoice} onFocus={prewarmVoice} disabled={loading === "search" || loading === "assistant"} aria-label="Speak your shopping list" className="relative grid size-14 shrink-0 place-items-center rounded-full bg-accent text-on-accent shadow-[0_12px_30px_-14px_rgb(var(--accent-shadow)/.8)] transition hover:bg-accent-hover active:scale-[.96] disabled:cursor-not-allowed disabled:opacity-45"><motion.span animate={{ scale: [1,1.08,1], opacity: [.35,0,.35] }} transition={{ duration: 2.2, repeat: Infinity }} className="absolute inset-0 rounded-full border border-accent" /><Microphone size={23} /></button>}</div>
          <p className="mt-2.5 hidden text-center text-[10px] text-muted sm:block">Hindi · English · Hinglish &nbsp;·&nbsp; You approve every purchase</p>
        </motion.form>}
      </AnimatePresence>
    </div>
    </motion.div>

    <AccountSettingsSheet
      notifications={notifications}
      open={accountSettingsOpen}
      initialSection={settingsSection}
      onOpenChange={(open) => { setAccountSettingsOpen(open); if (!open) setSettingsSection(null); }}
      identity={{ name: userName, email: user?.primaryEmailAddress?.emailAddress, imageUrl: user?.hasImage ? user.imageUrl : undefined }}
      commerceMode={commerceMode}
      languageLabel={replyLanguage.replace(/^./, (letter) => letter.toUpperCase())}
      stores={[
        { id: "swiggy", name: "Swiggy Instamart", connected: connected === true, detail: commerceMode === "demo" ? "Demo catalogue" : connected ? (selectedAddress ? `Delivering to ${addressLabel(selectedAddress)}` : "Choose an address") : "Not connected" },
        { id: "zepto", name: "Zepto", connected: zeptoConnected === true, detail: commerceMode === "demo" ? "Demo catalogue" : zeptoConnected ? (selectedZeptoAddress ? `Delivering to ${addressLabel(selectedZeptoAddress)}` : "Choose an address") : "Not connected" },
      ]}
      connectingStoreId={loading === "connection" && connected === false ? "swiggy" : zeptoBusy && zeptoConnected === false ? "zepto" : null}
      storeError={storeConnectError}
      onConnectStore={(storeId) => { setStoreConnectError(""); void beginProviderConnection(storeId === "zepto" ? "zepto" : "swiggy").then((problem) => { if (problem) setStoreConnectError(problem); }); }}
      onDisconnectStore={(storeId) => setDisconnectConfirm({ store: storeId === "zepto" ? "zepto" : "swiggy", open: true })}
      onChooseStoreAddress={(storeId) => setAddressPicker(storeId === "zepto" ? "zepto" : "swiggy")}
      onLanguageChange={(language) => {
        if (!user) return;
        const currentProfile = user.unsafeMetadata.gharsaathiProfile as Record<string, unknown> | undefined;
        void user.update({ unsafeMetadata: { ...user.unsafeMetadata, gharsaathiProfile: { ...currentProfile, language } } });
      }}
      deliveryLine={deliveryLine}
      onOpenDeliveryDirections={() => openDirections(headerAddressView.store ?? "zepto")}
      riderAssistAvailable={riderCalls.available}
      onSignOut={() => void signOut({ redirectUrl: "/" })}
    />

    <AddressPickerDialog
      open={addressPicker === "swiggy" && connected === true}
      store="swiggy"
      commerceMode={commerceMode}
      addresses={addresses}
      selectedId={addressId}
      refreshing={loading === "addresses"}
      emptyText={message}
      onSelect={(id) => void selectAddress(id)}
      onRefresh={() => void loadAddresses(true)}
      onClose={() => setAddressPicker(null)}
    />
    <AddressPickerDialog
      open={addressPicker === "zepto" && zeptoConnected === true}
      store="zepto"
      commerceMode={commerceMode}
      addresses={zeptoAddresses}
      selectedId={zeptoAddressId}
      refreshing={zeptoBusy}
      emptyText="Refresh once to save your Zepto addresses. Reloads after that stay local."
      onSelect={(id) => void selectZeptoAddress(id)}
      onRefresh={() => void loadZeptoAddresses(true)}
      onAddNew={commerceMode === "live" ? () => setAddressPopupOpen(true) : undefined}
      onClose={() => setAddressPicker(null)}
    />
    <ConfirmDialog
      open={disconnectConfirm.open}
      tone="destructive"
      title={disconnectText.title}
      message={disconnectText.body}
      confirmLabel={disconnectText.confirmLabel}
      busyLabel={disconnectText.busyLabel}
      busy={disconnectConfirm.store === "zepto" ? zeptoBusy : loading === "disconnect"}
      onClose={() => setDisconnectConfirm((current) => ({ ...current, open: false }))}
      onConfirm={() => {
        void (disconnectConfirm.store === "zepto" ? disconnectZepto() : disconnect()).then((done) => { if (done) setDisconnectConfirm((current) => ({ ...current, open: false })); });
      }}
    />
    <InputDialog open={directionsStore !== null} title="Directions for this address" description="What the delivery line tells the rider. Saved for this address only." closeLabel="Close directions" onClose={() => setDirectionsStore(null)}>
      {directionsStore && <DeliveryLinePanel
        provider={directionsStore}
        address={(() => { const selected = directionsStore === "zepto" ? selectedZeptoAddress : selectedAddress; return selected ? { id: selected.id, label: selected.tag || selected.category, text: selected.address_line } : null; })()}
        recipientName={userName}
        onChangeAddress={() => setAddressPicker(directionsStore)}
      />}
    </InputDialog>
    <CartSheet
      open={cartOpen}
      onClose={() => setCartOpen(false)}
      preferred={cartProvider}
      baskets={baskets}
      commerceMode={commerceMode}
      stores={{ zepto: { connected: zeptoConnected === true, addressId: zeptoAddressId }, swiggy_instamart: { connected: connected === true, addressId } }}
      orderInFlightStore={orderCard && orderCardInFlight(orderCard) ? orderCard.confirmation.provider : null}
      onBasketChange={handleBasketChange}
      onSwap={(provider, line) => { setCartOpen(false); startSwap(provider, line); }}
      onSaveAsList={() => quick.openLists("save-basket")}
      onBill={(provider, request) => { followScrollRef.current = true; setVoiceApproval({ provider, request }); showBasketAtLatest(provider, "bill_ready"); }}
      chips={chips}
      chipsBusyKey={quick.busyKey}
      chipsDisabled={chipsLocked}
      onChip={(chip) => { setCartOpen(false); followScrollRef.current = true; void quick.run(chip); }}
    />
    <SavedListsDialog open={quick.lists.open} mode={quick.lists.mode} basketStores={basketStores} addingListId={quick.addingListId} onClose={quick.closeLists} onAddList={(list) => void quick.addList(list)} onChanged={() => void quick.reload()} />
    <SupportDialog open={supportOpen} onClose={() => setSupportOpen(false)} onTrackOrder={() => { setSupportOpen(false); setTrackingOpen(true); }} />
    <AddressFormSheet open={addressPopupOpen} prefillRequest={addressPrefillRequest} onAdded={applyAddedZeptoAddress} onClose={closeAddressPopup} onPrefillApplied={handleAddressPrefillApplied} />
    <AnimatePresence>
      {setupOpen && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(event) => { if (event.target === event.currentTarget) setSetupOpen(false); }} className="fixed inset-0 z-40 flex items-end justify-end bg-scrim/30 backdrop-blur-sm md:p-4">
          <motion.section initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 40, opacity: 0 }} transition={{ type: "spring", stiffness: 100, damping: 20 }} className="max-h-[88dvh] w-full overflow-y-auto rounded-t-[2rem] bg-surface p-6 shadow-[0_30px_80px_-30px_rgb(var(--shadow)/.5)] md:max-w-md md:rounded-[2rem]">
            <header className="flex items-start">
              <div><p className="text-[10px] font-semibold tracking-[.18em] text-accent-ink">SET UP GHARSAATHI</p><h2 className="font-display mt-2 text-3xl font-semibold tracking-[-.04em]">Ready for your home</h2></div>
              <button onClick={() => setSetupOpen(false)} className="ml-auto grid size-10 place-items-center rounded-full border border-border" aria-label="Close shopping connections"><X size={19} /></button>
            </header>
            <p className="mt-4 text-sm leading-relaxed text-muted">{commerceMode === "demo" ? "Safe demo mode uses local sample data. Search, basket, and bill flows make zero Swiggy or Zepto API/MCP calls." : "Connect both once. They stay available together—every request checks all ready stores, with no provider switch. GharSaathi never asks for your password or OTP."}</p>

            <div className="mt-6 space-y-3">
              <section className="rounded-2xl border border-border bg-surface p-4" aria-label="Swiggy Instamart connection">
                <div className="flex gap-3"><span className={`mt-1 size-2.5 shrink-0 rounded-full ${connected ? "bg-success" : "bg-subtle"}`} /><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-3"><h3 className="font-semibold">Swiggy Instamart</h3><span className={`text-[10px] font-semibold ${connected ? "text-success" : "text-muted"}`}>{connected === null ? "Checking…" : commerceMode === "demo" ? "Demo data" : connected ? "Connected" : "Not connected"}</span></div><p className="mt-1 text-xs leading-5 text-muted">{commerceMode === "demo" ? "Static products and deterministic bills. Your Swiggy account and cart are untouched." : "Live search and final-bill review are available here."}</p></div></div>
                {commerceMode === "demo" ? <p className="mt-3 rounded-xl bg-success-soft px-3 py-2 text-[11px] leading-5 text-success">Provider calls are disabled. Launch <strong>make dev-live</strong> only when you intentionally want live data.</p> : connected ? <button type="button" onClick={() => setDisconnectConfirm({ store: "swiggy", open: true })} disabled={loading === "disconnect"} className="mt-3 text-xs font-semibold text-danger-hover disabled:opacity-45">{loading === "disconnect" ? "Disconnecting…" : "Disconnect Swiggy"}</button> : <button onClick={() => void beginProviderConnection("swiggy")} disabled={loading === "connection"} className="mt-4 flex w-full items-center justify-center gap-2 rounded-full bg-accent px-5 py-3 text-sm font-semibold text-on-accent active:scale-[.98] disabled:opacity-45">{loading === "connection" ? "Checking callback…" : "Connect Swiggy securely"} <ArrowRight size={17} /></button>}
              </section>

              <section className="rounded-2xl border border-border bg-surface p-4" aria-label="Zepto connection">
                <div className="flex gap-3"><span className={`mt-1 size-2.5 shrink-0 rounded-full ${zeptoConnected ? "bg-success" : "bg-subtle"}`} /><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-3"><h3 className="font-semibold">Zepto</h3><span className={`text-[10px] font-semibold ${zeptoConnected ? "text-success" : "text-muted"}`}>{zeptoConnected === null ? "Checking…" : commerceMode === "demo" ? "Demo safe" : zeptoSearchReady ? "Search ready" : zeptoConnected ? "Connected" : "Not connected"}</span></div><p className="mt-1 text-xs leading-5 text-muted">{commerceMode === "demo" ? "Static Zepto results are available without provider calls." : "Live product search uses one persistent MCP session and a cached tool catalogue."}</p></div></div>
                {commerceMode === "demo" ? <p className="mt-3 rounded-xl bg-success-soft px-3 py-2 text-[11px] leading-5 text-success">No Zepto OAuth, cart, payment, or order call is made in this mode.</p> : zeptoConnected ? <><p className="mt-3 rounded-xl bg-success-soft px-3 py-2 text-[11px] leading-5 text-success">Search is ready. A live COD order is available only after an exact bill approval and a separate final confirmation.</p><button type="button" onClick={() => setDisconnectConfirm({ store: "zepto", open: true })} disabled={zeptoBusy} className="mt-3 text-xs font-semibold text-danger-hover disabled:opacity-45">{zeptoBusy ? "Disconnecting…" : "Disconnect Zepto"}</button></> : <button onClick={() => void beginProviderConnection("zepto")} disabled={zeptoBusy} className="mt-4 flex w-full items-center justify-center gap-2 rounded-full border border-accent-border bg-white px-5 py-3 text-sm font-semibold text-accent-strong transition hover:bg-canvas active:scale-[.98] disabled:opacity-45">{zeptoBusy ? "Checking callback…" : "Connect Zepto securely"} <ArrowRight size={17} /></button>}
              </section>
            </div>

            {(connected || zeptoConnected) && <section className="mt-7 space-y-3" aria-label="Delivery addresses">
              {connected && <StoreAddressRow store="swiggy" commerceMode={commerceMode} addresses={addresses} selectedId={addressId} loading={loading === "addresses"} onChoose={() => setAddressPicker("swiggy")} />}
              {zeptoConnected && <StoreAddressRow store="zepto" commerceMode={commerceMode} addresses={zeptoAddresses} selectedId={zeptoAddressId} loading={zeptoBusy} onChoose={() => setAddressPicker("zepto")} />}
              <button type="button" onClick={() => setSetupOpen(false)} disabled={!((connected && addressId) || (zeptoConnected && zeptoAddressId))} className="w-full rounded-full bg-accent px-5 py-3 text-sm font-semibold text-on-accent disabled:opacity-40">Use these addresses</button>
            </section>}
            <PersonalizationOnboarding commerceMode={commerceMode} swiggyConnected={connected === true} zeptoConnected={zeptoConnected === true} />
          </motion.section>
        </motion.div>
      )}
    </AnimatePresence>
  </main>;
}

type OrderCardItem = { kind: "order-card"; card: LiveOrderCard };

function withOrderCard<T extends { kind: string; message?: { id: string } }>(items: T[], card: LiveOrderCard | null): (T | OrderCardItem)[] {
  if (!card) return items;
  const index = orderCardIndex(items, card.afterMessageId);
  return [...items.slice(0, index), { kind: "order-card", card }, ...items.slice(index)];
}
