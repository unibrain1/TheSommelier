# Menu plan setup

How to get your meal plan from Google Calendar into The Sommelier.

[Back to the README](../README.md)

The sync reads your meal plan from a Google Calendar through its secret `.ics` URL. You can add meals by hand, or use a recipe manager such as [Paprika](https://www.paprikaapp.com).

## Using Paprika (recommended)

Paprika is a recipe manager that can sync meal plans to Google Calendar.

1. In Paprika, go to **Settings → Calendar → Google Calendar**.
2. Sign in with your Google account. Select or create a calendar, for example "Menu Plan".
3. Plan your meals in the Paprika meal planner. Paprika copies them to the calendar.

## Using any calendar

Add meal events to any Google Calendar. Name each event after the meal, for example "Grilled Lamb Chops". See the [Menu Guide](menu-guide.md) for tips on entries that pair well.

## Getting the calendar .ics URL

1. Open [Google Calendar](https://calendar.google.com) in a browser.
2. Click the **⋮** menu next to your meal plan calendar, then **Settings and sharing**.
3. Scroll to **Integrate calendar**.
4. Copy the **Secret address in iCal format**. It looks like this:
   ```
   https://calendar.google.com/calendar/ical/...%40group.calendar.google.com/private-.../basic.ics
   ```
5. Add it to your `.env` as `GOOGLE_CALENDAR_ICS_URL`. Keep it private.
