import { h } from 'vue'
import PixelSprite from './PixelSprite.vue'
import FeatureStageTabs from './slots/FeatureStageTabs.vue'
import StageTrack from './slots/StageTrack.vue'
import './tokens.css'
import './features.css'

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
    FeatureStageTabs,
    StageTrack,
  },
  glyphs: {
    portfolio: glyph('grid'),
    project: glyph('bot'),
    'project.none': glyph('tray'),
    'task.running': glyph('bot.running'),
    'task.ready': glyph('bot'),
    'task.blocked': glyph('lock'),
    'feature.building': glyph('bot'),
    'defect.wild': glyph('bug'),
  },
}
