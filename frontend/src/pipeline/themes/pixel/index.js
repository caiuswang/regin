import { h } from 'vue'
import PixelSprite from './PixelSprite.vue'
import OverviewLoop from './slots/OverviewLoop.vue'
import ProjectsPanel from './slots/ProjectsPanel.vue'
import './tokens.css'

// A glyph is a sprite drawn where <PlGlyph kind="…"> sits; PlGlyph's own
// `kind` prop is dropped so it does not land on the <svg> as an attribute.
function glyph(name) {
  const Glyph = (_, { attrs }) => {
    const { kind: _kind, ...rest } = attrs
    return h(PixelSprite, { ...rest, name })
  }
  Glyph.displayName = `PixelGlyph(${name})`
  return Glyph
}

export default {
  slots: {
    OverviewLoop,
    ProjectsPanel,
  },
  glyphs: {
    portfolio: glyph('grid'),
    project: glyph('bot'),
    'project.none': glyph('tray'),
  },
}
