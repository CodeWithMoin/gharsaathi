"use client";

import Image from "next/image";
import { FormEvent, useCallback, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEV_USER_ID = process.env.NEXT_PUBLIC_DEV_USER_ID ?? "local-test-user";

type Address = {
  id: string;
  address_line: string;
  category?: string;
  tag?: string;
};

type AddressResponse = {
  addresses: Address[];
  resolution: { default_address_id?: string };
};

type Product = {
  product_id: string;
  sku_id: string;
  spin_id: string;
  name: string;
  brand?: string;
  pack_size?: string;
  price: { mrp: number; offer_price: number; unit_level_price?: string };
  available: boolean;
  image_url?: string;
  rating_value?: string;
  rating_count?: string;
  delivery_value?: string;
  delivery_unit?: string;
};

type SearchResponse = {
  products: Product[];
  similar_products: Product[];
  next_offset?: string;
  applied_memories?: Array<{ id: string; memory_type: string; text: string }>;
  provider_query?: string;
  excluded_product_count?: number;
};

type MemoryProposal = {
  id: string;
  memory_type: string;
  text: string;
  value: Record<string, string>;
  status: "proposed" | "confirmed" | "rejected";
};

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("X-User-Id", DEV_USER_ID);
  const response = await fetch(`${API}${path}`, { cache: "no-store", ...init, headers });
  const payload = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    const detail = payload && typeof payload.detail === "string" ? payload.detail : "Request failed.";
    throw new Error(detail);
  }
  return payload as T;
}

export default function Home() {
  const [connected, setConnected] = useState<boolean | null>(null);
  const [addresses, setAddresses] = useState<Address[]>([]);
  const [addressId, setAddressId] = useState("");
  const [query, setQuery] = useState("Fortune cooking oil 1 litre");
  const [results, setResults] = useState<Product[]>([]);
  const [preference, setPreference] = useState("I prefer Fortune oil in 1 litre packs.");
  const [memoryProposals, setMemoryProposals] = useState<MemoryProposal[]>([]);
  const [appliedMemories, setAppliedMemories] = useState<SearchResponse["applied_memories"]>([]);
  const [loading, setLoading] = useState<"connection" | "addresses" | "search" | "disconnect" | "memory" | null>("connection");
  const [searched, setSearched] = useState(false);
  const [message, setMessage] = useState("Checking your Swiggy connection…");

  const loadAddresses = useCallback(async () => {
    setLoading("addresses");
    setMessage("Loading your saved delivery addresses…");
    try {
      const data = await apiRequest<AddressResponse>("/api/swiggy/addresses");
      setAddresses(data.addresses);
      const preferred = data.resolution.default_address_id;
      if (preferred && data.addresses.some((address) => address.id === preferred)) {
        setAddressId(preferred);
      } else if (data.addresses.length === 1) {
        setAddressId(data.addresses[0].id);
      }
      setMessage(data.addresses.length ? "Choose where Instamart should deliver." : "No saved Swiggy addresses were found.");
    } catch (error) {
      const text = error instanceof Error ? error.message : "Could not load addresses.";
      if (text.toLowerCase().includes("connect")) setConnected(false);
      setMessage(text);
    } finally {
      setLoading(null);
    }
  }, []);

  useEffect(() => {
    let active = true;
    apiRequest<{ connected: boolean }>("/api/swiggy/connection")
      .then((data) => {
        if (!active) return;
        setConnected(data.connected);
        setMessage(data.connected ? "Swiggy is connected." : "Connect Swiggy to search live Instamart products.");
        if (data.connected) void loadAddresses();
      })
      .catch((error: unknown) => {
        if (!active) return;
        setConnected(false);
        setMessage(error instanceof Error ? error.message : "Could not check the connection.");
      })
      .finally(() => {
        if (active) setLoading((current) => current === "connection" ? null : current);
      });
    return () => { active = false; };
  }, [loadAddresses]);

  function connect() {
    window.location.assign(`${API}/api/swiggy/oauth/start`);
  }

  async function disconnect() {
    setLoading("disconnect");
    try {
      await apiRequest<null>("/api/swiggy/disconnect", { method: "POST" });
      setConnected(false);
      setAddresses([]);
      setAddressId("");
      setResults([]);
      setAppliedMemories([]);
      setSearched(false);
      setMessage("Swiggy has been disconnected.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not disconnect Swiggy.");
    } finally {
      setLoading(null);
    }
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!addressId) {
      setMessage("Choose a saved address first.");
      return;
    }
    setLoading("search");
    setSearched(false);
    setResults([]);
    setAppliedMemories([]);
    setMessage("Searching live Instamart availability…");
    try {
      const data = await apiRequest<SearchResponse>("/api/swiggy/search/personalized", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, address_id: addressId, offset: 0 }),
      });
      setResults(data.products);
      setAppliedMemories(data.applied_memories ?? []);
      setSearched(true);
      setMessage(data.products.length ? `${data.products.length} variants found.` : "No matching products found at this address.");
    } catch (error) {
      const text = error instanceof Error ? error.message : "Search failed.";
      if (text.toLowerCase().includes("connect")) setConnected(false);
      setMessage(text);
    } finally {
      setLoading(null);
    }
  }

  async function extractPreference(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading("memory");
    setMessage("Checking for an explicit shopping preference…");
    try {
      const data = await apiRequest<{ proposals: MemoryProposal[] }>("/api/memory/extract", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: preference, conversation_id: "web-preferences" }),
      });
      setMemoryProposals(data.proposals);
      setMessage(data.proposals.length ? "Review before GharCart remembers anything." : "No durable preference found. Try saying “I prefer…” or “Never buy…”.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not extract a preference.");
    } finally {
      setLoading(null);
    }
  }

  async function decideMemory(memory: MemoryProposal, confirm: boolean) {
    setLoading("memory");
    try {
      await apiRequest(`/api/memory/${memory.id}/${confirm ? "confirm" : "reject"}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(confirm ? {} : { reason: "User rejected the proposal." }),
      });
      if (confirm) {
        await apiRequest(`/api/memory/permissions/${encodeURIComponent(memory.memory_type)}/personalize_search`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ allowed: true, retention_days: 365 }),
        });
      }
      setMemoryProposals((current) => current.filter((item) => item.id !== memory.id));
      setMessage(confirm ? "Preference confirmed and enabled for relevant searches." : "Preference rejected and not used.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not update this preference.");
    } finally {
      setLoading(null);
    }
  }

  return (
    <main>
      <header className="hero">
        <p className="eyebrow">GHARCART · INSTAMART SEARCH</p>
        <h1>Ghar ka samaan, <span>seedha dhoondo.</span></h1>
        <p className="lede">Connect your Swiggy account, pick a saved address, and compare live product variants. Nothing is added to a cart and no order can be placed.</p>
      </header>

      <section className="panel connection" aria-busy={loading === "connection"}>
        <div>
          <p className="step">01 · ACCOUNT</p>
          <h2>{connected ? "Swiggy connected" : "Connect Swiggy"}</h2>
          <p>Sign-in happens on Swiggy. GharCart never asks for or sees your OTP.</p>
        </div>
        {connected ? (
          <button className="secondary" onClick={disconnect} disabled={loading !== null}>Disconnect</button>
        ) : (
          <button onClick={connect} disabled={loading === "connection"}>Connect securely</button>
        )}
      </section>

      {connected && (
        <section className="panel">
          <div className="sectionHeading">
            <div><p className="step">02 · DELIVERY</p><h2>Saved address</h2></div>
            <button className="textButton" onClick={loadAddresses} disabled={loading !== null}>Refresh</button>
          </div>
          {loading === "addresses" ? <div className="skeleton large" /> : addresses.length > 0 ? (
            <div className="addressGrid">
              {addresses.map((address) => (
                <label className={addressId === address.id ? "address active" : "address"} key={address.id}>
                  <input type="radio" name="address" value={address.id} checked={addressId === address.id} onChange={() => setAddressId(address.id)} />
                  <span><strong>{address.tag || address.category || "Saved address"}</strong><small>{address.address_line}</small></span>
                </label>
              ))}
            </div>
          ) : <p className="empty">Add a delivery address in Swiggy, then refresh here.</p>}
        </section>
      )}

      {connected && addresses.length > 0 && (
        <section className="panel">
          <p className="step">03 · MEMORY</p>
          <h2>What should I remember?</h2>
          <p>Only explicit shopping preferences are proposed. You approve each one before it can affect search.</p>
          <form onSubmit={extractPreference} className="searchForm">
            <label htmlFor="preference">Preference in English, Hindi, or Hinglish</label>
            <div className="searchRow">
              <input id="preference" value={preference} onChange={(event) => setPreference(event.target.value)} minLength={2} maxLength={4000} required />
              <button type="submit" disabled={loading !== null}>{loading === "memory" ? "Checking…" : "Review preference"}</button>
            </div>
          </form>
          {memoryProposals.length > 0 && <div className="memoryList">
            {memoryProposals.map((memory) => <article className="memoryCard" key={memory.id}>
              <div><small>{memory.memory_type.replaceAll("_", " ")}</small><strong>{memory.text}</strong><p>{Object.values(memory.value).join(" · ")}</p></div>
              <div className="memoryActions"><button className="secondary" type="button" onClick={() => decideMemory(memory, false)} disabled={loading !== null}>Reject</button><button type="button" onClick={() => decideMemory(memory, true)} disabled={loading !== null}>Confirm & use</button></div>
            </article>)}
          </div>}
        </section>
      )}

      {connected && addresses.length > 0 && (
        <section className="panel">
          <p className="step">04 · SEARCH</p>
          <h2>What do you need?</h2>
          <form onSubmit={search} className="searchForm">
            <label htmlFor="query">Product, brand, or category</label>
            <div className="searchRow">
              <input id="query" value={query} onChange={(event) => setQuery(event.target.value)} minLength={2} maxLength={120} required />
              <button type="submit" disabled={loading !== null || !addressId}>{loading === "search" ? "Searching…" : "Search Instamart"}</button>
            </div>
          </form>
        </section>
      )}

      <p className={message.toLowerCase().includes("failed") || message.toLowerCase().includes("could not") ? "notice error" : "notice"} role="status" aria-live="polite">{message}</p>

      {loading === "search" && <section className="productGrid" aria-label="Loading products">{[1, 2, 3].map((item) => <div className="product skeleton" key={item} />)}</section>}
      {searched && results.length === 0 && <section className="emptyState"><h2>Kuch nahi mila</h2><p>Try a broader product name or a different brand.</p></section>}
      {results.length > 0 && (
        <section className="results" aria-label="Instamart products">
          <div className="resultsHeading"><p className="step">LIVE RESULTS</p><h2>Available variants</h2></div>
          {appliedMemories && appliedMemories.length > 0 && <p className="memoryApplied">Personalized using: {appliedMemories.map((memory) => memory.text).join(" · ")}</p>}
          <div className="productGrid">
            {results.map((product) => (
              <article className="product" key={`${product.product_id}-${product.sku_id}`}>
                <div className="imageWrap">
                  {product.image_url ? <Image src={product.image_url} alt="" fill sizes="(max-width: 700px) 100vw, 30vw" unoptimized /> : <span>No image</span>}
                  <span className={product.available ? "availability" : "availability unavailable"}>{product.available ? "Available" : "Unavailable"}</span>
                </div>
                <div className="productBody">
                  {product.brand && <p className="brand">{product.brand}</p>}
                  <h3>{product.name}</h3>
                  {product.pack_size && <p className="pack">{product.pack_size}</p>}
                  <div className="price"><strong>₹{product.price.offer_price}</strong>{product.price.mrp > product.price.offer_price && <s>₹{product.price.mrp}</s>}</div>
                  {(product.rating_value || product.delivery_value) && <p className="meta">{product.rating_value && `★ ${product.rating_value}`}{product.rating_value && product.delivery_value && " · "}{product.delivery_value && `${product.delivery_value} ${product.delivery_unit ?? ""}`}</p>}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
