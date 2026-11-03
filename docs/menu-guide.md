# Menu guide

How to write Google Calendar menu entries for the best wine pairings.

[Back to the README](../README.md)

## Setup

The sync reads your meals from a Google Calendar (see [Menu plan setup](menu-plan.md)). It finds food words in each event and uses them to suggest a wine. [How it works](how-it-works.md#food-wine-pairing) explains the results.

## Best format

| Field | Example |
|---|---|
| **Title** | `Grilled Lamb Chops` |
| **Description** (optional) | `Italian style, with risotto` |

The sync reads both the title and the description, so you can be brief.

## Tips

- **Lead with the protein**: "Salmon with asparagus" works, "Dinner" doesn't
- **Add cuisine when relevant**: "Thai green curry" triggers different pairings than just "curry"
- **Cooking method helps**: "Braised short ribs" and "Grilled steak" both match on both the protein and the method
- **Several keywords add up**: "Grilled lamb" matches both "grilled" (bold red or rosé) and "lamb" (bold red). Both support the same suggestion.
- **More detail gives better pairings**: A descriptive title gives the sync more to match.

## Recognized keywords

The sync matches these words in the title and the description.
### Proteins
beef, steak, ribeye, filet, brisket, burger, short ribs, ribs, corned beef, lamb, veal, venison, elk, bison, pork, sausage, chops, pulled pork, carnitas, bacon, ham, chicken, turkey, duck, quail, cornish hen, salmon, tuna, halibut, cod, trout, swordfish, mahi, shrimp, crab, lobster, scallops, oysters, mussels, clams, fish, seafood, shellfish

### Cuisines
italian, french, japanese, thai, mexican, indian, chinese, korean, mediterranean, greek, spanish, bbq, barbecue, grilled, braised, roasted, smoked

### Dishes / Styles
salad, soup, pasta, risotto, pizza, taco, tacos, burrito, enchilada, quesadilla, curry, stew, chili, casserole, sandwich, lasagna, noodle, ramen, pho, udon, soba, mac, shepherd, beans, lentil, tofu, tempeh, vegetarian, vegan

## Not supported

The sync does not match side dishes that stand alone as an entry, or desserts.
