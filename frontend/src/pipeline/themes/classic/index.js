// Classic is the shell as it shipped before themes: every slot is the existing
// component, imported in place. Its props and emits are the contract a theme's
// override must honour.
import OverviewLoop from '../../../components/pipeline/OverviewLoop.vue'
import StageTrack from '../../../components/pipeline/StageTrack.vue'
import FeatureStageTabs from '../../../components/pipeline/FeatureStageTabs.vue'
import ProjectsPanel from '../../../components/pipeline/ProjectsPanel.vue'
import ProjectScopePanel from '../../../components/pipeline/ProjectScopePanel.vue'
import ChoiceSelect from '../../../components/pipeline/ChoiceSelect.vue'

export default {
  slots: {
    OverviewLoop,
    StageTrack,
    FeatureStageTabs,
    ProjectsPanel,
    ProjectScopePanel,
    ChoiceField: ChoiceSelect,
    SecretStage: null,
  },
  // Empty on purpose: a classic glyph is whatever icon its call site already
  // draws, passed to <PlGlyph> as the default slot, so classic cannot drift
  // from what each site rendered before.
  glyphs: {},
}
