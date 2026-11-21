// Which data a link's page loads first, so it can start loading when the link is pressed.
// Each path must be exactly the string the page passes to swr(), or the prefetch is wasted.
import { query } from "./api.js";
import { todayLocal } from "./format.js";

export function dataPathFor(href) {
  if (!href?.startsWith("#")) return null;
  const [tab, search = ""] = href.slice(1).split("?");
  const id = new URLSearchParams(search).get("id");
  if (tab === "events") return id ? `/events/${encodeURIComponent(id)}` : `/events${query({ date_from: todayLocal() })}`;
  if (tab === "members") return id ? `/members/${encodeURIComponent(id)}/attendance` : search ? null : "/members";
  if (tab === "overview" || tab === "") return "/dashboard";
  if (tab === "groups") return "/groups";
  return null;
}
