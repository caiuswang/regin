import { h } from 'vue'
import PixelSprite from './PixelSprite.vue'
import FeatureStageTabs from './slots/FeatureStageTabs.vue'
import StageTrack from './slots/StageTrack.vue'
import OverviewLoop from './slots/OverviewLoop.vue'
import ProjectsPanel from './slots/ProjectsPanel.vue'
import ProjectScopePanel from './slots/ProjectScopePanel.vue'
import ChoiceField from './slots/ChoiceField.vue'
import MenuField from './slots/MenuField.vue'
import SecretStage from './slots/SecretStage.vue'
import DefectsHead from './slots/DefectsHead.vue'
import FeatureTableHead from './slots/FeatureTableHead.vue'
import FeatureStats from './slots/FeatureStats.vue'
import './tokens.css'
import './features.css'
import './tasks.css'
import './dialogs.css'
import './menus.css'
import './defects.css'
import './projects.css'

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
    OverviewLoop,
    ProjectsPanel,
    ProjectScopePanel,
    ChoiceField,
    MenuField,
    SecretStage,
    DefectsHead,
    FeatureTableHead,
    FeatureStats,
  },
  glyphs: {
    portfolio: glyph('grid'),
    project: glyph('bot'),
    'project.none': glyph('tray'),
    'task.ready': glyph('bot'),
    'task.running': glyph('bot.running'),
    'task.blocked': glyph('lock'),
    'task.failed': glyph('bot.failed'),
    'task.idea': glyph('bulb'),
    'task.done': glyph('check'),
    'feature.building': glyph('bot'),
    'defect.wild': glyph('bug'),
    'defect.closed': glyph('bug.cold'),
    'dialog.feature': glyph('flag'),
    'dialog.task': glyph('bulb'),
    'dialog.arm': glyph('bot'),
    'priority.urgent': glyph('bars.4'),
    'priority.high': glyph('bars.3'),
    'priority.medium': glyph('bars.2'),
    'priority.low': glyph('bars.1'),
    'priority.none': glyph('bars.0'),
  },
}
