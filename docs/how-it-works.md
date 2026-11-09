# How it works

How the sync builds the plan, how it ranks wines, and how it pairs wine with food.

[Back to the README](../README.md)

Each sync runs these steps:

1. Fetch your CellarTracker inventory, notes and food tags, and your Google Calendar menu. The sync fetches the menu only when the menu feature is on.
2. Build a 52-week plan from drinking windows, seasons, evolution tracks and holidays.
3. Ask Claude for a short tasting note for each bottle, if Claude is on.
4. Compare the plan with your inventory. If the menu feature is on, suggest a pairing for each meal.
5. Publish the complete plan to the site.

The sync builds the plan again from scratch each time. CellarTracker is the source of all wine data. Claude writes the tasting notes. The rules do the rest, so the same cellar on the same day gives the same plan, apart from the Claude notes.

## How wines are prioritized

The sync scores every bottle in your cellar and schedules the 52 best wines, one for each week. A lower score means the wine goes earlier. The score has five parts:

| Part | Weight |
| --- | --- |
| Drinking window | 50% |
| Seasonal fit | 25% |
| Diversity | 10% |
| CellarTracker rating | 10% |
| Community signals | 5% |

### Drinking window

This part matters most. It compares the current year with the drinking window from CellarTracker.

- **Past peak** — The wine is fading. It scores highest, and more so the further it is past its window.
- **In window** — Wines near the middle of the window score higher than wines that just entered it. Wines near the end of the window score higher as the end gets closer.
- **Before window** — The wine is not ready. It scores low, and lowest when it is 3 or more years from its window.
- **No data** — The wine gets a neutral score.

### Seasonal fit

Sparkling, rosé and white wines suit spring and summer. Bold reds, such as Cabernet, Syrah, Barolo and Nebbiolo, suit fall and winter. Light reds, such as Pinot Noir and Gamay, suit all year.

### Diversity

The plan avoids the same wine, producer or variety in close weeks. A match with a wine from the last 5 weeks lowers the score. The penalty is largest for the same wine, smaller for the same producer, and smallest for the same variety. It fades each week.

### CellarTracker rating

A higher community rating gives a small boost. A wine without a rating gets the average. This part has little weight, so urgency and season matter far more than ratings.

### Community signals

The CellarTracker community notes add a small signal. It uses recent score changes, how fast new notes arrive, and notes that suggest the drinking window has moved. Without the [community notes feature](features.md#cellartracker-community-notes), this part has no effect.

### Scheduling order

The sync fills the plan in three stages, so the most urgent bottles get a place first:

1. **Weeks 1–8** — Only past-peak and expiring wines.
2. **Weeks 9–16** — Wines that expire soon also join.
3. **Weeks 17–52** — All other wines, ranked by score.

Evolution tracks and holidays reserve their weeks first. See [Plan configuration](plan-configuration.md).

### Bottles that the plan holds back

The plan never schedules some bottles, even if they score well:

- **Long-agers.** A wine that is more than 2 years from its drinking window waits.
- **Many bottles.** If you have 3 or more bottles of one wine, the plan holds back 2 and schedules the rest. It always schedules at least 1.

## Food-wine pairing

The sync compares your meal plan with your cellar and suggests a wine for each meal. If Claude is on, it also reads the menu text for details such as the protein, the preparation and the richness. Without Claude, it matches keywords in the meal title and description.

- The suggestion favors bottles that need drinking soon.
- Each meal gets a different bottle.
- The sync uses the variety of each planned wine to improve the match.
- Each suggestion shows a confidence level: high, medium or low. With Claude, the level is higher when the meal has more food details to score. Keyword matching is always medium. A meal with no food words is low.

Each meal has one of three results:

- **Pairs Well** (green) — The planned wine matches the meal.
- **Sommelier Pick** (blue) — The planned wine does not match, or matches only in part, so the sync suggests a better bottle from your cellar.
- **No Match** (muted) — The meal has no known food words, or no other bottle in your cellar fits it better. Enjoy the planned wine.

To write meal entries that pair well, see the [Menu Guide](menu-guide.md).
