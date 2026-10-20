package com.vanilo.psych.agent.service;

import org.junit.jupiter.api.Test;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;

class KnowledgeSourcePolicyTests {
    private final Map<String,String> sleep = Map.of(
        "license", "public-domain-nhlbi",
        "licenseSource", "https://www.nhlbi.nih.gov/about/contact/trademark-branding-and-logo",
        "publisher", "National Heart, Lung, and Blood Institute (NHLBI)");

    @Test void acceptsReviewedSourcesAndLegacyNimh() {
        assertTrue(KnowledgeSourcePolicy.accepts("https://www.nhlbi.nih.gov/health/insomnia", sleep));
        assertTrue(KnowledgeSourcePolicy.accepts("https://www.nimh.nih.gov/health/topics/anxiety-disorders",
            Map.of("license", "public-domain-nimh")));
        assertTrue(KnowledgeSourcePolicy.accepts("https://www.cdc.gov/emotional-well-being/improve-your-emotional-well-being/",
            Map.of("license", "public-domain-cdc", "licenseSource", "https://www.cdc.gov/other/agencymaterials.html",
                   "publisher", "Centers for Disease Control and Prevention (CDC)")));
        assertTrue(KnowledgeSourcePolicy.accepts("https://www.nccih.nih.gov/health/relaxation",
            Map.of("license", "public-domain-nccih", "licenseSource", "https://www.nccih.nih.gov/tools/privacy",
                   "publisher", "National Center for Complementary and Integrative Health (NCCIH)")));
    }

    @Test void rejectsSpoofedScopeAndLicense() {
        for (String url : new String[]{"http://www.nhlbi.nih.gov/health/insomnia",
            "https://www.nhlbi.nih.gov.evil.test/health/insomnia", "https://www.nhlbi.nih.gov:9000/health/sleep",
            "https://user:pass@www.nhlbi.nih.gov/health/sleep", "https://www.nhlbi.nih.gov/news/test",
            "https://www.nhlbi.nih.gov/health/../admin", "https://www.nhlbi.nih.gov/health/%2e%2e/admin",
            "https://www.nhlbi.nih.gov/health/sleep?track=yes", "https://www.nhlbi.nih.gov/health/sleep#part"}) {
            assertFalse(KnowledgeSourcePolicy.accepts(url, sleep), url);
        }
        assertFalse(KnowledgeSourcePolicy.accepts("https://www.nhlbi.nih.gov/health/insomnia", Map.of("license", "public-domain-nhlbi")));
        assertFalse(KnowledgeSourcePolicy.accepts("https://www.nhlbi.nih.gov/health/insomnia", Map.of("license", "public-domain-nimh")));
    }
}
