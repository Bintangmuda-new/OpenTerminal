# Visual foundations

- Use black panel surfaces and subtle `#2A313B` borders. Panel corner radius is 5 px; smaller controls use 2–3 px.
- Keep content dense. Common widget inset is 8–12 px horizontally and 4–6 px vertically. Avoid adding padding unless it resolves a real overlap.
- Widget titles use bold 10 px Segoe UI, centered in their allocated content column. Never let a full-width title overlap adjacent status text.
- Numeric market data uses Consolas. Positive values are green, negative values red, and caution/neutral states amber.
- Secondary labels use cool gray. Do not introduce gradients, card shadows, or decorative icons.
- Hover states brighten foreground or border colors. Existing buttons use restrained dark fills and pale text.
- Startup and connection transitions use opacity/translation animations; live data should not animate decoratively.
- Scale existing responsive values using the current window size logic. Allow adjacent columns to size to their content instead of overlapping.

## Assets

The existing application icon is `desktop/assets/openterminal.ico`. No additional icon assets were invented or copied for this tweak.
