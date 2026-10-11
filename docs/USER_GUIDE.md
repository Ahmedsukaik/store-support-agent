# Team inbox: a guide for the support team

The chat assistant answers customers on the store's website. It can look things up and ask
for changes, but it cannot change an order or move money. Those wait for you here.

## Approvals

Every return, exchange, cancellation and address change the assistant starts appears as a
card. Nothing happens to the order until you decide. (In the demo's guided mode, without AI,
address changes go to a person as a handoff instead.)

A card shows:

- **What is asked**, for which order and which customer (the email is partly hidden).
- **The amount**, worked out by the store from the order and the policy, never by the
  assistant. For a change of mind or the wrong size, the $6.95 return fee is already deducted.
- **The checks the store ran**: the customer was verified, the item is inside the 30-day
  window, nothing is final sale, the new size is in stock.
- **Flags** that need a second look, such as refunds on one order adding up to more than
  $300, which need a manager.

**Approve** applies the change and tells the customer in the chat (for a return: the label is
on its way and when the refund arrives). **Reject** needs a short reason, which the customer
sees, so write it for them. If something changed since the request (the size sold out, the
order shipped), approving is refused and the page tells you why.

## Handoffs

The assistant passes the chat to you when it should not decide alone: a damaged item, a lost
parcel, a warranty claim, a request for an exception, a customer who asked for a person, or a
customer who stays unhappy. The system also hands a chat over by itself after three failed
attempts to verify a customer, or when a reply was held. The card has a summary of who the
customer is, the order, what they want and what was already checked, so you do not need to
read the whole chat.

While you have the chat, the assistant stays silent and the customer's messages come to you.
Write your reply in the card; the customer sees it signed by the team. When you are done,
**Hand back to the assistant**.

### Held replies

Before any reply reaches the customer, it is checked:

- every amount in it must come from the store: the order, the policy or your messages (not
  from what the customer typed), and an amount called a refund must be the refund the store
  worked out;
- it must not say a refund or change is done when nobody approved it.

If a reply fails, the customer gets a short message that a teammate will check, and you get a
handoff with the held draft and the reason (for example "mentions $25.00, which is not in the
order data, the help center or the team's messages"). Answer the customer yourself, and tell
whoever maintains the assistant, since a held reply usually means the instructions or the
help center need a fix.

## Cost

The Cost tab shows what the chat cost in AI usage, the number of model calls and tool calls,
and how many tokens came from the cache (cached tokens cost a tenth). Most chats cost a
fraction of a cent.

## Orders and activity

**Orders** shows the store as it is now, including approved returns and cancellations.
**Activity** lists every step: each lookup, each request, each refusal and each decision, with
who made it (the assistant, the customer, the team, or the system). Use it to answer "why did
the assistant say that?"
