# Privacy

Glowcast pairs with your Hue Bridge once and stores that key in Home Assistant. The key gives full access to the bridge, like any app you connect to it. Removing Glowcast from Home Assistant does not remove its app from the bridge; see [Uninstalling](uninstalling.md) for how to remove it.

The TV's capture call only works on your local network, after the TV pairing you already approved for Home Assistant. Frames are kept in memory and never stored or sent anywhere. Separately: if privacy matters to you, consider turning off **Live Plus** (LG's ACR feature) in your TV settings. That one does send viewing data to LG.

The diagnostics you can download for a problem report leave out keys, addresses, ids, serial numbers and names; see [Diagnostics](status.md#diagnostics).

## This website

This website counts its visits with [Cloudflare Web Analytics](https://www.cloudflare.com/web-analytics/), to see how many people visit and which sites they come from. It sets no cookies and stores nothing in your browser for this. Each page loads one script from Cloudflare, which reports the visit to Cloudflare: the page, the site you came from, your browser and your country. Not who you are, and the site has no other analytics or tracking. Your choice of light or dark mode is kept in your own browser.

This is only about the website. Glowcast itself, in Home Assistant, never sends anything anywhere.

## Disclaimer

Glowcast is an independent project. It is not affiliated with, endorsed by or connected to Signify (Philips Hue) or LG Electronics. All product names are trademarks of their respective owners.

Glowcast is open source under the [MIT licence](https://github.com/nicohabets/glowcast/blob/main/LICENSE).
