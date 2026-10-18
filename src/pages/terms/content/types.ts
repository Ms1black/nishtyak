export interface TermsSection {
  id: string
  title: string
  paragraphs?: string[]
  items?: string[]
}

export interface TermsContent {
  title: string
  updated: string
  intro: string
  tocLabel: string
  sections: TermsSection[]
}
