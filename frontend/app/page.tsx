"use client";

import { AnimatePresence, motion } from "framer-motion";
import { ArrowRight, Basket, ChatCircleDots, Check, Microphone, PaperPlaneRight, Plus, ShieldCheck, ShoppingCartSimple, Storefront, X } from "@phosphor-icons/react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Brand } from "./brand";
import { addBasketLine, BasketPanel, type ProviderName, type ShoppingBasket } from "./basket/BasketPanel";
import { groupProductsForComparison, ProductComparison, type Product } from "./product-comparison";
import { LiveOrderTracking } from "./order-tracking";
import { PersonalizationOnboarding } from "./personalization-onboarding";
import { SupportPanel } from "./support-panel";
import { VoicePanel, type VoiceTranscriptTurn } from "./voice-panel";
import { conversationIntent, conversationReply } from "./conversation-intent.mjs";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEV_USER_ID = process.env.NEXT_PUBLIC_DEV_USER_ID ?? "local-test-user";
const LEGACY_CHAT_STORAGE_KEY = "gharsaathi:conversation:v1";

type Address = { id: string; address_line: string; category?: string; tag?: string };
type CommerceMode = "demo" | "live";
type ChatMessage = { id: string; role: "user" | "assistant"; content: string; modality: "text" | "voice"; created_at: string };
type PersistedConversation = { id: string; submitted_query: string; products: Product[]; searched: boolean; messages: ChatMessage[]; results_after_message_id?: string | null; updated_at: string };
type LegacyConversation = { submittedQuery?: string; products?: Product[]; searched?: boolean };
let swiggyConnectionRequest: Promise<{ connected: boolean; mode?: CommerceMode }> | null = null;
let zeptoConnectionRequest: Promise<{ connected: boolean; mode?: CommerceMode }> | null = null;

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("X-User-Id", DEV_USER_ID);
  const response = await fetch(`${API}${path}`, { cache: "no-store", ...init, headers });
  const payload = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new ApiError(payload && typeof payload.detail === "string" ? payload.detail : "Request failed.", response.status);
  return payload as T;
}

class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

const suggestions = ["Monthly groceries", "Shampoo and batteries", "Pet supplies"];

export default function Home() {
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [basketOpen, setBasketOpen] = useState(false);
  const [basket, setBasket] = useState<ShoppingBasket | null>(null);
  const [basketProvider, setBasketProvider] = useState<ProviderName>("swiggy_instamart");
  const [addingSku, setAddingSku] = useState<string | null>(null);
  const [setupOpen, setSetupOpen] = useState(false);
  const [supportOpen, setSupportOpen] = useState(false);
  const [trackingOpen, setTrackingOpen] = useState(false);
  const [connected, setConnected] = useState<boolean | null>(null);
  const [commerceMode, setCommerceMode] = useState<CommerceMode>("demo");
  const [zeptoConnected, setZeptoConnected] = useState<boolean | null>(null);
  const [zeptoSearchReady, setZeptoSearchReady] = useState(false);
  const [zeptoBusy, setZeptoBusy] = useState(false);
  const [zeptoAddresses, setZeptoAddresses] = useState<Address[]>([]);
  const [zeptoAddressId, setZeptoAddressId] = useState("");
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [addressId, setAddressId] = useState("");
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [products, setProducts] = useState<Product[]>([]);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [resultsAfterMessageId, setResultsAfterMessageId] = useState<string | null>(null);
  const [loading, setLoading] = useState<"connection" | "addresses" | "search" | "disconnect" | null>("connection");
  const [searched, setSearched] = useState(false);
  const [chatHydrated, setChatHydrated] = useState(false);
  const [message, setMessage] = useState("Checking your Swiggy connection…");
  const addressRequestRef = useRef<Promise<void> | null>(null);
  const zeptoAddressRequestRef = useRef<Promise<void> | null>(null);
  const searchRequestsRef = useRef(new Map<string, Promise<void>>());
  const lastSearchRef = useRef<{ key: string; at: number } | null>(null);
  const conversationSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const conversationSaveTimerRef = useRef<number | null>(null);
  const chatMessagesRef = useRef<ChatMessage[]>([]);
  const selectedAddress = useMemo(() => addresses.find((address) => address.id === addressId), [addresses, addressId]);
  const closeVoice = useCallback(() => setVoiceOpen(false), []);
  const handleBasketTouched = useCallback(() => setBasketOpen(true), []);
  const handleVoiceResults = useCallback((searchQuery: string, foundProducts: Product[]) => {
    setSubmittedQuery(searchQuery);
    setProducts(foundProducts);
    setSearched(true);
    setResultsAfterMessageId(chatMessagesRef.current.at(-1)?.id ?? null);
    setMessage(`${foundProducts.length} ${commerceMode === "demo" ? "demo" : "live"} options found.`);
  }, [commerceMode]);
  const handleVoiceTranscript = useCallback((turn: VoiceTranscriptTurn) => {
    setChatMessages((current) => {
      const index = current.findIndex((item) => item.id === turn.id);
      if (index === -1) {
        const next = [...current, { ...turn, modality: "voice" as const, created_at: new Date().toISOString() }];
        chatMessagesRef.current = next;
        return next;
      }
      const next = [...current];
      next[index] = { ...next[index], role: turn.role, content: turn.content };
      chatMessagesRef.current = next;
      return next;
    });
  }, []);

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
      }
    })();
    addressRequestRef.current = request;
    void request.finally(() => {
      if (addressRequestRef.current === request) addressRequestRef.current = null;
    });
    return request;
  }, [commerceMode]);

  const loadZeptoAddresses = useCallback(async (refresh = false) => {
    if (zeptoAddressRequestRef.current) return zeptoAddressRequestRef.current;
    const request = (async () => {
      setZeptoBusy(true);
      try {
        const data = await apiRequest<{ addresses: Address[]; resolution: { default_address_id?: string }; needs_refresh?: boolean }>(`/api/zepto/addresses${refresh ? "?refresh=true" : ""}`);
        setZeptoAddresses(data.addresses);
        const preferred = data.resolution.default_address_id;
        if (preferred && data.addresses.some((item) => item.id === preferred)) setZeptoAddressId(preferred);
        else if (data.addresses.length === 1) setZeptoAddressId(data.addresses[0].id);
        setZeptoSearchReady(data.addresses.length > 0);
        if (refresh && data.addresses.length) setMessage("Zepto addresses saved. Reload will use this snapshot.");
        else if (data.needs_refresh) setMessage("Tap Refresh from Zepto once to save your delivery addresses.");
      } catch (cause) {
        setZeptoSearchReady(false);
        setMessage(cause instanceof Error ? cause.message : "Could not load Zepto addresses.");
      } finally {
        setZeptoBusy(false);
      }
    })();
    zeptoAddressRequestRef.current = request;
    void request.finally(() => {
      if (zeptoAddressRequestRef.current === request) zeptoAddressRequestRef.current = null;
    });
    return request;
  }, []);

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
  }, [loadAddresses]);

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
          if (data.connected) void loadZeptoAddresses();
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
  }, [loadZeptoAddresses]);

  useEffect(() => {
    let active = true;
    void apiRequest<PersistedConversation | null>("/api/conversations/current")
      .then((conversation) => {
        if (!active) return;
        if (conversation) {
          const restoredIntent = conversationIntent(conversation.submitted_query);
          const localReply = restoredIntent === "connection_status"
            ? "Manage stores mein current Swiggy aur Zepto connection status dikh raha hai."
            : conversationReply(conversation.submitted_query, { swiggyConnected: false, zeptoConnected: false });
          const restoredMessages = conversation.messages?.length ? conversation.messages : conversation.submitted_query ? [{ id: `migrated-${conversation.id}`, role: "user" as const, content: conversation.submitted_query, modality: "text" as const, created_at: conversation.updated_at }] : [];
          const repairedMessages = localReply && restoredMessages.at(-1)?.content !== localReply
            ? [...restoredMessages, { id: `local-reply-${Date.now().toString(36)}`, role: "assistant" as const, content: localReply, modality: "text" as const, created_at: new Date().toISOString() }]
            : restoredMessages;
          const fallbackResultAnchor = [...repairedMessages].reverse().find((item) => item.role === "user")?.id ?? null;
          setSubmittedQuery(localReply ? "" : conversation.submitted_query);
          setProducts(localReply ? [] : conversation.products);
          setSearched(localReply ? false : conversation.searched);
          setChatMessages(repairedMessages);
          chatMessagesRef.current = repairedMessages;
          setResultsAfterMessageId(localReply ? null : conversation.results_after_message_id ?? fallbackResultAnchor);
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
        setProducts(migratedProducts);
        setSearched(migratedSearched);
        const migratedMessages: ChatMessage[] = [{ id: `legacy-${Date.now().toString(36)}`, role: "user", content: legacy.submittedQuery, modality: "text", created_at: new Date().toISOString() }];
        setChatMessages(migratedMessages);
        void apiRequest<PersistedConversation>("/api/conversations/current", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ submitted_query: legacy.submittedQuery, products: migratedProducts, searched: migratedSearched, messages: migratedMessages }),
        }).then(() => window.localStorage.removeItem(LEGACY_CHAT_STORAGE_KEY));
      })
      .catch((cause: unknown) => {
        if (active) setMessage(cause instanceof Error ? cause.message : "Could not restore this chat.");
      })
      .finally(() => { if (active) setChatHydrated(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    void apiRequest<ShoppingBasket>("/api/basket?provider=swiggy_instamart")
      .then((savedBasket) => {
        if (active) setBasket(savedBasket);
      })
      .catch(() => {
        // The basket panel will show a detailed error if the user opens it.
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!chatHydrated || (!submittedQuery && !chatMessages.length)) return;
    const timer = window.setTimeout(() => {
      conversationSaveTimerRef.current = null;
      const snapshot = { submitted_query: submittedQuery, products, searched, messages: chatMessages, results_after_message_id: resultsAfterMessageId };
      conversationSaveQueueRef.current = conversationSaveQueueRef.current
        .catch(() => undefined)
        .then(async () => {
          await apiRequest<PersistedConversation>("/api/conversations/current", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(snapshot),
          });
        })
        .catch((cause: unknown) => {
          setMessage(cause instanceof Error ? cause.message : "Could not save this chat.");
        });
    }, 250);
    conversationSaveTimerRef.current = timer;
    return () => {
      window.clearTimeout(timer);
      if (conversationSaveTimerRef.current === timer) conversationSaveTimerRef.current = null;
    };
  }, [chatHydrated, chatMessages, products, resultsAfterMessageId, searched, submittedQuery]);

  useEffect(() => {
    if (!chatHydrated || (!submittedQuery && !chatMessages.length && !basketOpen && !voiceOpen && !supportOpen && !trackingOpen)) return;
    let frame = window.requestAnimationFrame(() => {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const destination = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      if (reducedMotion) {
        window.scrollTo(0, destination);
        return;
      }

      const origin = window.scrollY;
      const distance = destination - origin;
      const duration = 700;
      const startedAt = window.performance.now();
      const animate = (now: number) => {
        const progress = Math.min(1, (now - startedAt) / duration);
        const eased = progress < 0.5
          ? 4 * progress * progress * progress
          : 1 - Math.pow(-2 * progress + 2, 3) / 2;
        window.scrollTo(0, origin + distance * eased);
        if (progress < 1) frame = window.requestAnimationFrame(animate);
      };
      frame = window.requestAnimationFrame(animate);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [basketOpen, chatHydrated, chatMessages, loading, products, submittedQuery, supportOpen, trackingOpen, voiceOpen]);

  async function startNewChat() {
    if (conversationSaveTimerRef.current !== null) {
      window.clearTimeout(conversationSaveTimerRef.current);
      conversationSaveTimerRef.current = null;
    }
    setSubmittedQuery("");
    setProducts([]);
    setSearched(false);
    setChatMessages([]);
    chatMessagesRef.current = [];
    setResultsAfterMessageId(null);
    setQuery("");
    try {
      await conversationSaveQueueRef.current;
      await apiRequest<null>("/api/conversations/new", { method: "POST" });
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not start a new chat.");
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
    } catch (cause) {
      setZeptoAddressId(previous);
      setMessage(cause instanceof Error ? cause.message : "Could not save this Zepto address.");
    }
  }

  async function runSearch(value: string) {
    const clean = value.trim();
    if (!clean) return;
    const localReply = conversationReply(clean, { swiggyConnected: connected === true, zeptoConnected: zeptoConnected === true });
    if (localReply) {
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
    const searchKey = `${addressId}\u0000${zeptoAddressId}\u0000${clean.toLocaleLowerCase()}`;
    const inFlight = searchRequestsRef.current.get(searchKey);
    if (inFlight) return inFlight;
    const now = Date.now();
    const lastSearch = lastSearchRef.current;
    if (lastSearch?.key === searchKey && now - lastSearch.at < 800) return;
    lastSearchRef.current = { key: searchKey, at: now };

    setSubmittedQuery(clean);
    const userMessageId = `text-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    setChatMessages((current) => {
      const next = [...current, {
        id: userMessageId,
        role: "user" as const,
        content: clean,
        modality: "text" as const,
        created_at: new Date().toISOString(),
      }];
      chatMessagesRef.current = next;
      return next;
    });
    setResultsAfterMessageId(userMessageId);
    setQuery("");
    setVoiceOpen(false);
    setProducts([]);
    setSearched(false);
    const swiggyReady = connected === true && Boolean(addressId);
    const zeptoReady = zeptoConnected === true && Boolean(zeptoAddressId);
    if (!swiggyReady && !zeptoReady) {
      setSetupOpen(true);
      setMessage("Connect a store and choose its saved delivery address first.");
      return;
    }

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
        const outcomes = await Promise.allSettled(searches);
        const combined = outcomes.flatMap((outcome) => outcome.status === "fulfilled" ? outcome.value : []);
        const failures = outcomes.filter((outcome): outcome is PromiseRejectedResult => outcome.status === "rejected");
        setProducts(combined);
        setSearched(true);
        if (combined.length) setMessage(`${combined.length} ${commerceMode === "demo" ? "demo" : "live"} options found across ready stores.`);
        else if (failures.length) setMessage(failures[0].reason instanceof Error ? failures[0].reason.message : "Store search failed.");
        else setMessage("No matching products found at the selected addresses.");
      } catch (cause) {
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
      setBasketProvider(provider);
      setBasket(next);
      setBasketOpen(false);
      setMessage(`${product.name} basket mein add ho gaya.`);
      return true;
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not add this item.");
      return false;
    } finally {
      setAddingSku(null);
    }
  }

  async function disconnect() {
    setLoading("disconnect");
    try {
      await apiRequest<null>("/api/swiggy/disconnect", { method: "POST" });
      swiggyConnectionRequest = null;
      setConnected(false); setAddresses([]); setAddressId(""); setProducts([]); setMessage("Swiggy disconnected.");
    } catch (cause) { setMessage(cause instanceof Error ? cause.message : "Could not disconnect Swiggy."); }
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
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : "Could not disconnect Zepto.");
    } finally {
      setZeptoBusy(false);
    }
  }

  async function beginProviderConnection(provider: "swiggy" | "zepto") {
    if (provider === "zepto") setZeptoBusy(true);
    else setLoading("connection");
    try {
      const readiness = await apiRequest<{ ready: boolean; message: string }>(`/api/${provider}/oauth/readiness`);
      if (!readiness.ready) {
        setMessage(readiness.message);
        return;
      }
      window.location.assign(`${API}/api/${provider}/oauth/start`);
    } catch (cause) {
      setMessage(cause instanceof Error ? cause.message : `Could not prepare ${provider} login.`);
    } finally {
      if (provider === "zepto") setZeptoBusy(false);
      else setLoading((value) => value === "connection" ? null : value);
    }
  }

  const connectionLabel = commerceMode === "demo" ? "Safe demo · no live calls" : connected && zeptoConnected ? "Swiggy + Zepto linked" : connected && selectedAddress ? selectedAddress.tag || selectedAddress.category || "Swiggy ready" : zeptoConnected ? "Zepto linked" : connected ? "Choose address" : "Connect stores";
  const readyStoreCount = Number(connected === true && Boolean(selectedAddress)) + Number(zeptoSearchReady);
  const basketCount = basket?.lines.reduce((total, line) => total + line.quantity, 0) ?? 0;
  const hasChat = Boolean(submittedQuery || chatMessages.length);
  const resultAnchorExists = resultsAfterMessageId !== null && chatMessages.some((item) => item.id === resultsAfterMessageId);
  const searchOutcome = loading === "search" ? (
    <motion.div key="searching-response" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 sm:mr-12"><div className="min-w-0 rounded-[8px_22px_22px_22px] border border-[#ebddd2] bg-[#fffdf9] px-5 py-4"><div className="flex items-center gap-1.5" aria-label="GharSaathi is searching">{[0,1,2].map((dot) => <motion.i key={dot} animate={{ y: [0,-4,0] }} transition={{ duration: .8, repeat: Infinity, delay: dot * .13 }} className="size-1.5 rounded-full bg-[#c6531d]" />)}<span className="ml-2 text-sm text-[#806f65]">{commerceMode === "demo" ? "Static demo catalogue mein dekh rahi hoon" : zeptoSearchReady ? "Swiggy aur Zepto par dekh rahi hoon" : "Swiggy Instamart par dekh rahi hoon"}</span></div></div></motion.div>
  ) : searched && !products.length ? (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 sm:mr-12"><div className="min-w-0 max-w-md rounded-[8px_22px_22px_22px] border border-[#ebddd2] bg-[#fffdf9] px-5 py-4"><div className="flex gap-3"><Basket size={20} className="mt-0.5 shrink-0 text-[#c6531d]" /><p className="text-sm leading-6">Is address par kuch nahi mila. Product ya brand ko thoda broadly bolkar try karein.</p></div></div></motion.div>
  ) : products.length ? (() => {
    const productCount = groupProductsForComparison(products).length;
    return <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 min-w-0 sm:mr-12"><div className="mb-2 w-fit max-w-full rounded-[18px] border border-[#ebddd2] bg-[#fffdf9] px-4 py-3"><p className="text-[13px] leading-5"><strong>{productCount === 1 ? "1 relevant option mila." : `${productCount} relevant options mile.`}</strong> {productCount > 1 ? (commerceMode === "demo" ? "Add karte hi agla item khulega." : `${zeptoSearchReady ? "Swiggy aur Zepto" : "Swiggy Instamart"} ke options compare karein.`) : "Best matching option neeche hai."}</p></div><ProductComparison products={products} commerceMode={commerceMode} addingSku={addingSku} onAdd={addProduct} /><p className="mt-2 flex items-start gap-2 px-1 text-[10px] leading-4 text-[#8e796d]"><ShieldCheck size={14} className="mt-0.5 shrink-0" /> Each store keeps its own basket and exact bill. Nothing is purchased without your approval.</p></motion.div>;
  })() : null;

  return <main className="grain min-h-[100dvh] bg-[#f8f2ea] text-[#35241c]">
    <header className="fixed inset-x-0 top-0 z-30 border-b border-[#e9dbcf]/70 bg-[#fffdf9]/92 shadow-[0_10px_35px_-30px_rgba(73,42,28,.7)] backdrop-blur-xl">
      <div className="mx-auto flex h-[68px] max-w-4xl items-center px-4 sm:px-6">
        <div className="flex min-w-0 items-center">
          <Brand />
          <span className={`ml-2 hidden size-2 shrink-0 rounded-full ring-4 ring-[#fffdf9] sm:block ${readyStoreCount > 0 ? "bg-[#4d945d]" : "bg-[#c9a995]"}`} role="status" aria-label={readyStoreCount > 0 ? `${readyStoreCount} store${readyStoreCount === 1 ? "" : "s"} ready` : "No store ready"} title={connectionLabel} />
          <span className="ml-2 hidden truncate text-[11px] text-[#806f65] sm:block">Your shopping chat</span>
        </div>
        <nav className="ml-auto flex shrink-0 items-center gap-0.5" aria-label="Chat actions">
          {hasChat && <button onClick={() => void startNewChat()} disabled={loading === "search"} aria-label="Start a new chat" className="grid size-10 place-items-center rounded-full text-[#806f65] transition hover:bg-[#f4e7dc] hover:text-[#35241c] active:scale-[.96] disabled:cursor-not-allowed disabled:opacity-40"><Plus size={19} /></button>}
          {basketCount > 0 && <button onClick={() => setBasketOpen((open) => !open)} aria-label={`Open basket with ${basketCount} items`} className="relative grid size-10 place-items-center rounded-full text-[#70584b] transition hover:bg-[#f4e7dc] active:scale-[.96]"><ShoppingCartSimple size={21} /><span className="absolute right-0 top-0 grid min-w-4 place-items-center rounded-full bg-[#c6531d] px-1 text-[9px] font-bold leading-4 text-white">{basketCount}</span></button>}
          <button type="button" onClick={() => setSupportOpen((open) => !open)} aria-label="Get help with an order" className={`grid size-10 place-items-center rounded-full transition active:scale-[.96] ${supportOpen ? "bg-[#f1dfd2] text-[#9f451d]" : "text-[#70584b] hover:bg-[#f4e7dc]"}`}><ChatCircleDots size={21} weight={supportOpen ? "fill" : "regular"} /></button>
          <button onClick={() => setSetupOpen(true)} aria-label={`Manage stores. ${connectionLabel}`} className={`relative ml-0.5 flex h-10 items-center justify-center gap-2 rounded-full border px-2.5 text-xs font-semibold transition active:scale-[.98] md:max-w-[190px] md:px-3 ${setupOpen ? "border-[#c98d6c] bg-[#f4e2d6] text-[#8f421b]" : "border-[#e4d3c7] bg-white text-[#5c463b] hover:border-[#d8bbaa]"}`}><Storefront size={18} className="shrink-0 text-[#c6531d]" /><span className="hidden truncate md:block">{connectionLabel}</span><span className={`absolute -right-0.5 -top-0.5 size-2.5 rounded-full border-2 border-[#fffdf9] ${readyStoreCount > 0 ? "bg-[#4d945d]" : "bg-[#c9a995]"}`} aria-hidden="true" /></button>
        </nav>
      </div>
    </header>

    <section className={`mx-auto flex min-h-[100dvh] max-w-4xl flex-col overflow-x-clip px-4 pt-[96px] sm:px-6 ${voiceOpen ? "pb-64 sm:pb-60" : "pb-40 sm:pb-44"}`}>
      <div className="flex-1">
        <div className="mb-8 flex items-center gap-3"><span className="text-[10px] font-semibold uppercase tracking-[.18em] text-[#9b8172]">Today</span><span className="h-px flex-1 bg-[#e8dcd2]" /></div>
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mr-8 sm:mr-12">
          <div className="min-w-0 max-w-[620px]">
            <div className="rounded-[8px_22px_22px_22px] border border-[#ebddd2] bg-[#fffdf9] px-5 py-4 shadow-[0_18px_45px_-34px_rgba(74,43,27,.7)]"><p className="text-[15px] leading-7 sm:text-base">Hello, Moin. <strong className="font-semibold">Aaj ghar ke liye kya mangwana hai?</strong></p><p className="mt-1.5 text-sm leading-6 text-[#806f65]">Bas bol dijiye—main ready stores par saath mein check karungi.</p><button type="button" onClick={() => setSetupOpen(true)} className="mt-3 flex flex-wrap items-center gap-2 text-[10px] font-semibold"><span className={`rounded-full px-2.5 py-1 ${connected ? "bg-[#e7f2e8] text-[#397144]" : "bg-[#f3e9e2] text-[#8a7569]"}`}>Swiggy {commerceMode === "demo" ? "demo" : connected ? "live" : "connect"}</span><span className={`rounded-full px-2.5 py-1 ${commerceMode === "demo" || zeptoSearchReady ? "bg-[#e7f2e8] text-[#397144]" : "bg-[#f3e9e2] text-[#8a7569]"}`}>Zepto {commerceMode === "demo" ? "demo" : zeptoSearchReady ? "live" : zeptoConnected ? "linked" : "connect"}</span><span className="text-[#a15b36]">Manage</span></button></div>
            {!hasChat && <div className="mt-3 flex flex-wrap gap-2">{suggestions.map((suggestion) => <button key={suggestion} onClick={() => void runSearch(suggestion)} disabled={loading === "search"} className="rounded-full border border-[#dfcbbd] bg-[#fffaf5]/70 px-3.5 py-2 text-xs font-medium text-[#70584b] transition hover:border-[#c99e84] hover:bg-white active:scale-[.98] disabled:cursor-not-allowed disabled:opacity-45">{suggestion}</button>)}</div>}
          </div>
        </motion.div>

        <AnimatePresence initial={false}>
          {chatMessages.map((chatMessage) => (
            <div key={chatMessage.id} className="contents">
              {chatMessage.role === "user" ? (
                <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="mt-8 flex justify-end pl-8 sm:pl-12">
                  <div className="max-w-full whitespace-pre-wrap rounded-[22px_8px_22px_22px] bg-[#3b2a22] px-5 py-3.5 text-[15px] leading-6 text-[#fffaf5] shadow-[0_18px_36px_-24px_rgba(53,36,28,.8)]">{chatMessage.content}</div>
                </motion.div>
              ) : (
                <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="mt-8 mr-8 min-w-0 sm:mr-12">
                  <div className="max-w-[620px] whitespace-pre-wrap rounded-[8px_22px_22px_22px] border border-[#ebddd2] bg-[#fffdf9] px-5 py-4 text-[15px] leading-7 shadow-[0_18px_45px_-34px_rgba(74,43,27,.7)] sm:text-base">{chatMessage.content}</div>
                </motion.div>
              )}
              {chatMessage.id === resultsAfterMessageId && searchOutcome}
            </div>
          ))}
          {!resultAnchorExists && searchOutcome}
        </AnimatePresence>

        <AnimatePresence>{basketOpen && <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} className="mt-8 mr-8 min-w-0 sm:mr-12"><BasketPanel provider={basketProvider} addressId={basketProvider === "zepto" ? zeptoAddressId : addressId} commerceMode={commerceMode} onBasketChange={setBasket} /><button type="button" onClick={() => setBasketOpen(false)} className="mx-auto mt-3 block text-xs font-semibold text-[#806f65] transition hover:text-[#3b2a22]">Hide basket</button></motion.div>}</AnimatePresence>
        <AnimatePresence>{supportOpen && <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} className="mt-8 mr-8 min-w-0 sm:mr-12"><SupportPanel onClose={() => setSupportOpen(false)} onTrackOrder={() => { setSupportOpen(false); setTrackingOpen(true); }} /></motion.div>}</AnimatePresence>
        <AnimatePresence>{trackingOpen && <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 8 }} className="mt-8 mr-8 min-w-0 sm:mr-12"><LiveOrderTracking initialAddressId={addressId || undefined} onClose={() => setTrackingOpen(false)} /></motion.div>}</AnimatePresence>
      </div>
    </section>

    <div className="fixed inset-x-0 bottom-0 z-20 bg-gradient-to-t from-[#f8f2ea] via-[#f8f2ea] to-transparent px-4 pb-4 pt-10 sm:pb-6">
      <AnimatePresence mode="wait">
        {voiceOpen ? <VoicePanel key="voice-composer" addressId={addressId} zeptoAddressId={zeptoAddressId} onClose={closeVoice} onResults={handleVoiceResults} onTranscriptTurn={handleVoiceTranscript} onBasketTouched={handleBasketTouched} /> : <motion.form key="text-composer" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} onSubmit={(event: FormEvent) => { event.preventDefault(); void runSearch(query); }} className="mx-auto max-w-3xl">
          <div className="flex items-center gap-2 rounded-[26px] border border-[#deccbf] bg-[#fffdf9] p-2 pl-5 shadow-[0_24px_60px_-28px_rgba(70,40,24,.55),inset_0_1px_0_rgba(255,255,255,.9)]"><input value={query} onChange={(event) => setQuery(event.target.value)} disabled={loading === "search"} className="min-w-0 flex-1 bg-transparent py-2 text-[15px] outline-none placeholder:text-[#a28e82] disabled:cursor-not-allowed disabled:opacity-60" placeholder="Type or just tap the mic…" aria-label="Shopping request" />{query.trim() ? <button type="submit" aria-label="Send request" disabled={loading === "search"} className="grid size-12 shrink-0 place-items-center rounded-full bg-[#3b2a22] text-white transition active:scale-[.96] disabled:cursor-not-allowed disabled:opacity-45"><PaperPlaneRight size={20} weight="fill" /></button> : <button type="button" onClick={() => setVoiceOpen(true)} disabled={loading === "search"} aria-label="Speak your shopping list" className="relative grid size-14 shrink-0 place-items-center rounded-full bg-[#c6531d] text-white shadow-[0_12px_30px_-14px_rgba(158,65,24,.8)] transition hover:bg-[#b94a18] active:scale-[.96] disabled:cursor-not-allowed disabled:opacity-45"><motion.span animate={{ scale: [1,1.08,1], opacity: [.35,0,.35] }} transition={{ duration: 2.2, repeat: Infinity }} className="absolute inset-0 rounded-full border border-[#c6531d]" /><Microphone size={23} /></button>}</div>
          <p className="mt-2.5 text-center text-[10px] text-[#8d7a6f]">Hindi · English · Hinglish &nbsp;·&nbsp; You approve every purchase</p>
        </motion.form>}
      </AnimatePresence>
    </div>

    <AnimatePresence>
      {setupOpen && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(event) => { if (event.target === event.currentTarget) setSetupOpen(false); }} className="fixed inset-0 z-40 flex items-end justify-end bg-[#35241c]/30 backdrop-blur-sm md:p-4">
          <motion.section initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 40, opacity: 0 }} transition={{ type: "spring", stiffness: 100, damping: 20 }} className="max-h-[88dvh] w-full overflow-y-auto rounded-t-[2rem] bg-[#fffdf9] p-6 shadow-[0_30px_80px_-30px_rgba(53,36,28,.5)] md:max-w-md md:rounded-[2rem]">
            <header className="flex items-start">
              <div><p className="text-[10px] font-semibold tracking-[.18em] text-[#c6531d]">SET UP GHARSAATHI</p><h2 className="font-display mt-2 text-3xl font-semibold tracking-[-.04em]">Ready for your home</h2></div>
              <button onClick={() => setSetupOpen(false)} className="ml-auto grid size-10 place-items-center rounded-full border border-[#e9dbcf]" aria-label="Close shopping connections"><X size={19} /></button>
            </header>
            <p className="mt-4 text-sm leading-relaxed text-[#806f65]">{commerceMode === "demo" ? "Safe demo mode uses local sample data. Search, basket, and bill flows make zero Swiggy or Zepto API/MCP calls." : "Connect both once. They stay available together—every request checks all ready stores, with no provider switch. GharSaathi never asks for your password or OTP."}</p>

            <div className="mt-6 space-y-3">
              <section className="rounded-2xl border border-[#e7d8cd] bg-[#fffaf5] p-4" aria-label="Swiggy Instamart connection">
                <div className="flex gap-3"><span className={`mt-1 size-2.5 shrink-0 rounded-full ${connected ? "bg-[#4d945d]" : "bg-[#c9a995]"}`} /><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-3"><h3 className="font-semibold">Swiggy Instamart</h3><span className={`text-[10px] font-semibold ${connected ? "text-[#397144]" : "text-[#806f65]"}`}>{connected === null ? "Checking…" : commerceMode === "demo" ? "Demo data" : connected ? "Connected" : "Not connected"}</span></div><p className="mt-1 text-xs leading-5 text-[#806f65]">{commerceMode === "demo" ? "Static products and deterministic bills. Your Swiggy account and cart are untouched." : "Live search and final-bill review are available here."}</p></div></div>
                {commerceMode === "demo" ? <p className="mt-3 rounded-xl bg-[#eef6ef] px-3 py-2 text-[11px] leading-5 text-[#397144]">Provider calls are disabled. Launch <strong>make dev-live</strong> only when you intentionally want live data.</p> : connected ? <button onClick={disconnect} disabled={loading === "disconnect"} className="mt-3 text-xs font-semibold text-[#9e4536] disabled:opacity-45">{loading === "disconnect" ? "Disconnecting…" : "Disconnect Swiggy"}</button> : <button onClick={() => void beginProviderConnection("swiggy")} disabled={loading === "connection"} className="mt-4 flex w-full items-center justify-center gap-2 rounded-full bg-[#c6531d] px-5 py-3 text-sm font-semibold text-white active:scale-[.98] disabled:opacity-45">{loading === "connection" ? "Checking callback…" : "Connect Swiggy securely"} <ArrowRight size={17} /></button>}
              </section>

              <section className="rounded-2xl border border-[#e7d8cd] bg-[#fffaf5] p-4" aria-label="Zepto connection">
                <div className="flex gap-3"><span className={`mt-1 size-2.5 shrink-0 rounded-full ${zeptoConnected ? "bg-[#4d945d]" : "bg-[#c9a995]"}`} /><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-3"><h3 className="font-semibold">Zepto</h3><span className={`text-[10px] font-semibold ${zeptoConnected ? "text-[#397144]" : "text-[#806f65]"}`}>{zeptoConnected === null ? "Checking…" : commerceMode === "demo" ? "Demo safe" : zeptoSearchReady ? "Search ready" : zeptoConnected ? "Connected" : "Not connected"}</span></div><p className="mt-1 text-xs leading-5 text-[#806f65]">{commerceMode === "demo" ? "Static Zepto results are available without provider calls." : "Live product search uses one persistent MCP session and a cached tool catalogue."}</p></div></div>
                {commerceMode === "demo" ? <p className="mt-3 rounded-xl bg-[#eef6ef] px-3 py-2 text-[11px] leading-5 text-[#397144]">No Zepto OAuth, cart, payment, or order call is made in this mode.</p> : zeptoConnected ? <><p className="mt-3 rounded-xl bg-[#eef6ef] px-3 py-2 text-[11px] leading-5 text-[#397144]">Search only. Cart sync, payment, and real order tools remain disabled.</p><button onClick={() => void disconnectZepto()} disabled={zeptoBusy} className="mt-3 text-xs font-semibold text-[#9e4536] disabled:opacity-45">{zeptoBusy ? "Disconnecting…" : "Disconnect Zepto"}</button></> : <button onClick={() => void beginProviderConnection("zepto")} disabled={zeptoBusy} className="mt-4 flex w-full items-center justify-center gap-2 rounded-full border border-[#d7a182] bg-white px-5 py-3 text-sm font-semibold text-[#8f421b] transition hover:bg-[#fff5ed] active:scale-[.98] disabled:opacity-45">{zeptoBusy ? "Checking callback…" : "Connect Zepto securely"} <ArrowRight size={17} /></button>}
              </section>
            </div>

            {connected && <section className="mt-7"><div className="flex items-center"><div><h3 className="font-display font-semibold">{commerceMode === "demo" ? "Demo delivery address" : "Swiggy delivery address"}</h3><p className="mt-1 text-[10px] text-[#8e796d]">{commerceMode === "demo" ? "Local fixture only" : "Reload uses the saved snapshot"}</p></div><button onClick={() => void loadAddresses(true)} disabled={loading === "addresses"} className="ml-auto text-xs font-semibold text-[#c6531d] disabled:cursor-not-allowed disabled:opacity-45">{loading === "addresses" ? "Refreshing…" : commerceMode === "demo" ? "Reset demo" : "Refresh from Swiggy"}</button></div>{loading === "addresses" ? <div className="mt-3 h-20 animate-pulse rounded-2xl bg-[#eee2d8]" /> : <div className="mt-3 divide-y divide-[#e9dbcf]">{addresses.map((address) => <label key={address.id} className="flex cursor-pointer items-start gap-3 py-4"><input type="radio" checked={addressId === address.id} onChange={() => void selectAddress(address.id)} className="mt-1 accent-[#c6531d]" /><span className="flex-1"><strong className="block text-sm">{address.tag || address.category || "Saved address"}</strong><small className="mt-1 block leading-relaxed text-[#806f65]">{address.address_line}</small></span>{addressId === address.id && <Check size={17} className="text-[#c6531d]" />}</label>)}</div>}{!addresses.length && loading !== "addresses" && <p className="mt-3 rounded-2xl bg-[#f7ede5] px-4 py-3 text-xs leading-5 text-[#806f65]">{message}</p>}<button onClick={() => setSetupOpen(false)} disabled={!addressId} className="mt-5 w-full rounded-full bg-[#c6531d] px-5 py-3 text-sm font-semibold text-white disabled:opacity-40">Use this address</button></section>}
            {zeptoConnected && <section className="mt-7 border-t border-[#eaded5] pt-6"><div className="flex items-center"><div><h3 className="font-display font-semibold">{commerceMode === "demo" ? "Zepto demo address" : "Zepto delivery address"}</h3><p className="mt-1 text-[10px] text-[#8e796d]">{commerceMode === "demo" ? "Local fixture only" : "Reload uses the saved snapshot—no MCP call"}</p></div><button onClick={() => void loadZeptoAddresses(true)} disabled={zeptoBusy} className="ml-auto text-xs font-semibold text-[#7b3fc4] disabled:cursor-not-allowed disabled:opacity-45">{zeptoBusy ? "Refreshing…" : commerceMode === "demo" ? "Reset demo" : "Refresh from Zepto"}</button></div>{zeptoBusy && !zeptoAddresses.length ? <div className="mt-3 h-20 animate-pulse rounded-2xl bg-[#eee2d8]" /> : <div className="mt-3 divide-y divide-[#e9dbcf]">{zeptoAddresses.map((address) => <label key={address.id} className="flex cursor-pointer items-start gap-3 py-4"><input type="radio" checked={zeptoAddressId === address.id} onChange={() => void selectZeptoAddress(address.id)} className="mt-1 accent-[#7b3fc4]" /><span className="flex-1"><strong className="block text-sm">{address.tag || address.category || "Saved address"}</strong><small className="mt-1 block leading-relaxed text-[#806f65]">{address.address_line}</small></span>{zeptoAddressId === address.id && <Check size={17} className="text-[#7b3fc4]" />}</label>)}</div>}{!zeptoAddresses.length && !zeptoBusy && <p className="mt-3 rounded-2xl bg-[#f7ede5] px-4 py-3 text-xs leading-5 text-[#806f65]">Refresh once to save your Zepto addresses. Reloads after that stay local.</p>}<button onClick={() => setSetupOpen(false)} disabled={!zeptoAddressId} className="mt-5 w-full rounded-full bg-[#5f2e91] px-5 py-3 text-sm font-semibold text-white disabled:opacity-40">Use Zepto address</button></section>}
            <PersonalizationOnboarding commerceMode={commerceMode} swiggyConnected={connected === true} zeptoConnected={zeptoConnected === true} />
          </motion.section>
        </motion.div>
      )}
    </AnimatePresence>
  </main>;
}
