export const selectActiveAssets = (state) =>
  state.assets.items
    .filter((asset) => asset.is_active)
    .sort((a, b) => a.play_order - b.play_order)

export const selectInactiveAssets = (state) =>
  state.assets.items.filter((asset) => !asset.is_active)
