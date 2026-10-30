# Uninstalling

1. **Remove Glowcast's entry:** Settings → Devices & services → Glowcast → ⋮ → **Delete**. Do this for each TV or computer you added. If Glowcast was syncing, it releases the lights first.
2. **Remove its app from the Hue bridge.** Removing Glowcast from Home Assistant does not remove its app from the bridge (the bridge does not allow that), so Home Assistant shows a notification with these steps: in the Hue app, go to **Settings → Apps → Network-based** (Dutch: Instellingen → Toepassingen → Netwerk-gebaseerd), tap the info button next to **glowcast** and remove it. That app's key gives full access to the bridge, so do not skip this step.
3. **Remove Glowcast from HACS:** open Glowcast in HACS, open the menu (⋮) and choose **Remove**, then restart Home Assistant.

If you imported the blueprints "Start Glowcast when the TV turns on" or "Follow a Hue Sync Box", or wrote your own automations with Glowcast's entities, remove them too (Settings → Automations & scenes).
