import type { TermsContent } from './types'

export const termsEn: TermsContent = {
  title: 'Terms of use',
  updated: 'Version of October 16, 2026',
  tocLabel: 'Contents',
  intro:
    'nishtyak is a service that prepares Python projects to be run again by someone else: it analyzes uploaded code, helps you clarify dependencies and builds an archive with a README and requirements.txt. The service was developed as part of the MISIS x BMSTU x Nishtyak project. By using nishtyak you agree to the terms below.',
  sections: [
    {
      id: 'service',
      title: '1. What the service does',
      paragraphs: [
        'nishtyak performs static analysis: it reads source files, imports and dependency manifests. Uploaded code, install commands and build scripts are never executed by the service.',
        'The result is an archive to hand over to a colleague. The “Ready to verify” status means you have answered all analysis questions; it does not confirm that the project actually runs and produces the expected result. Only running it by the README can confirm that.',
      ],
    },
    {
      id: 'account',
      title: '2. Account and teams',
      items: [
        'You are responsible for actions taken under your account and for keeping your display name accurate.',
        'Whoever creates a team becomes its owner. The owner and admins manage members, roles and invite links.',
        'Anyone with a valid invite link can join the team. Do not publish links publicly and delete them when they are no longer needed.',
        'Team members see all current and future team projects.',
        'Deleting a team is irreversible: its projects, archives and invite links are deleted with it.',
      ],
    },
    {
      id: 'content',
      title: '3. Your projects',
      items: [
        'Rights to uploaded code and data remain with you or their rights holders.',
        'By uploading a project you confirm you are entitled to do so and allow the service to store and process it as needed for analysis and building the archive.',
        'The service looks for secrets (tokens, keys, passwords) and excludes such files from the archive by default, but detection is not guaranteed to be complete. Review your project before uploading and before sharing the archive.',
        'Texts you write in the project description go into the archive README.',
      ],
    },
    {
      id: 'prohibited',
      title: '4. What is not allowed',
      items: [
        'Uploading malicious code or materials that break the law or infringe the rights of others.',
        'Uploading personal data of third parties without a lawful basis.',
        'Trying to bypass service limits, access other people’s projects or disrupt the service, including by creating excessive load.',
      ],
    },
    {
      id: 'storage',
      title: '5. Storage and deletion',
      paragraphs: [
        'Uploaded archives, analysis results and saved answers are kept for a limited time set by the service operator and are then deleted automatically. Deleted data cannot be restored.',
        'Do not use nishtyak as the only place your project is stored — keep a copy yourself or in version control.',
      ],
    },
    {
      id: 'ai',
      title: '6. Automatic project description',
      paragraphs: [
        'If the operator enables automatic descriptions, the service sends DeepSeek filtered source excerpts and static analysis results after upload. Files with detected secrets are excluded from the transmitted context.',
        'The description is automatically included in README and can be edited before building. Model hypotheses do not confirm library versions, data availability or successful execution. If the model is unavailable, the service prepares a package from static analysis.',
      ],
    },
    {
      id: 'liability',
      title: '7. Liability',
      paragraphs: [
        'nishtyak is an educational project provided “as is”, without warranties of uninterrupted operation, completeness of analysis or fitness of the result for a particular purpose.',
        'The developers are not liable for damage arising from use of the service, including leaks of data you uploaded despite secret warnings, and errors in built archives.',
      ],
    },
    {
      id: 'changes',
      title: '8. Changes to these terms',
      paragraphs: [
        'These terms may be updated. The date of the current version is shown at the top of the page. By continuing to use the service after changes, you accept the new version.',
        'Send questions about these terms to the MISIS x BMSTU x Nishtyak project team.',
      ],
    },
  ],
}
