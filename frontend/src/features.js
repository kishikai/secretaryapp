// Tabs switched off for this deployment. Remove an id to bring its tab back; the features stay in the app.
export const HIDDEN_TABS = ["announcements", "messages", "notifications", "rules", "evidence"];

// Message collection (collector status, /stats, the Messages and Review tabs) only runs when the Messages tab is on.
export const MESSAGES_ENABLED = !HIDDEN_TABS.includes("messages");
