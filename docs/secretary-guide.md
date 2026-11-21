# Secretary's guide

This guide is for the person who runs attendance day to day. You don't need any technical knowledge. If something here doesn't match what you see, tell whoever set the app up for you.

## Signing in

Open the app's address in your browser and sign in with the username and password you were given. You stay signed in for 12 hours. Accounts are created by whoever looks after the app; ask them if someone new needs access.

There are three kinds of account:

| | Secretary | Assistant | Viewer |
|---|---|---|---|
| See members, events, registers, absences | ✓ | ✓ | ✓ |
| Download reports | ✓ | ✓ | ✓ |
| Add and edit members, groups and events | ✓ | ✓ | |
| Take attendance and save progress | ✓ | ✓ | |
| Prepare announcements | ✓ | ✓ | |
| Import members from a spreadsheet | ✓ | | |
| Finalise or reopen a register | ✓ | | |
| Deactivate members, delete groups or draft events | ✓ | | |
| Review messages, rules and notifications | ✓ | | |

## Your first week

The **Overview** page shows a "Getting started" list until you've done these once:

1. **Add your members.** Go to **Members**. Add people one at a time with **Add member**, or use **Import from spreadsheet**:
   - In Excel, choose *File → Save As → CSV UTF-8*.
   - The first row should hold column names. Only `full_name` (or `name`) is required. You can also have `email`, `phone`, `member_code`, `notes`, `joined_on`, `left_on` and `groups`. Write dates as 2025-03-31, and separate several groups with `;`.
   - Click **Check file**. Nothing is saved yet. Rows with problems are shown in red and skipped. Rows that look like someone already in the app are set to *Skip*, so importing the same file twice doesn't create duplicates. Change a row to *Update existing* if the file has newer details for that person.
   - Click **Confirm import**.
2. **Make groups**, such as Choir or Committee, under **Groups**, and tick people's groups on their member card.
3. **Create an event** under **Events** with **New event**. Save it as a draft while you're planning, or as scheduled when it's fixed.
4. **Choose who's expected.** On the event page, use **Add a group** or **Add people**. Adding a group copies who is in it today. If the group changes later, this event's list doesn't.

## Taking a register

1. Open the event (it must be scheduled, not a draft).
2. In **Register**, choose Present, Late, Absent, No practices or Excused for each person. **Mark everyone not yet recorded as present** fills in the rest in one go.
3. Click **Save progress** as often as you like. You can stop and come back.
4. When everyone has a status, click **Finalise register**. This locks it and marks the event completed. Only finalised registers count in absence figures.

Made a mistake on a finalised register? Click **Reopen register** and say why. Every change, finalise and reopen is listed under **Change history** with who did it and when.

**Late** counts as attending. **Excused** means they let you know; it's shown but doesn't count against them. **No practices** marks that person as not expected to practice for this event, so the event is left out of their attendance figures.

## Absences

The **Absences** page shows, for each person, how many counted events they were expected at and how often they were absent. Only finalised registers of events that weren't cancelled count. Absence rate = absent ÷ (present + late + absent).

Click a name to see every event behind their figures. Events that don't count yet are marked "Not counted". **No practices** is also listed in the member's history but excluded from the events-counted and absence-rate figures.

## Messages as evidence

If the message collector is running, messages that sound like attendance ("running late", "sorry, can't make it") appear under **Review**. The app suggests who, which event and which status. **Nothing changes until you approve it**, and you can change any part of the suggestion first. A missing message never means someone was absent.

If a register is already finalised, reopen it first. Approved messages are linked in that register's change history.

If the collector stops, a notice says so at the top of the page. You can carry on taking registers by hand as normal.

## Announcements

Under **Announcements**, choose an event and a starting template, edit the text, and check the preview. Then click **Copy message** and paste it into your Messenger group yourself. The app never sends anything. You can also copy the names or email addresses of the people it's for.

## Reports

Every list has a **Download** button: member list, event summary, attendance sheet, absence report and a member's history. The files open in Excel. Each one says when it was made and which filters and dates it covers, and ends with totals. A download includes only saved changes, so save the register first.

## Keeping data safe

- **Backups** are taken by whoever runs the app, ideally every night. If something goes badly wrong, such as a whole register lost or the computer failing, they can put back the last backup. Anything entered since that backup would need re-entering, so tell them what you did that day.
- **Who can see what:** everyone with an account can see members' contact details and attendance. Only give accounts to people who need them, and use viewer accounts for anyone who only needs to look.
- **Leaving members:** deactivate them rather than deleting them. Their attendance history stays correct, and they drop out of new events and the "Everyone active" announcement list.
- **How long data is kept:** at the moment everything is kept. Your organisation should decide how long to keep members' details and attendance after they leave. Until then, don't import more personal information than you need.
