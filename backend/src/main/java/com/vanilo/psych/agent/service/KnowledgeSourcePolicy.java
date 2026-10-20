package com.vanilo.psych.agent.service;

import java.net.URI;
import java.util.List;
import java.util.Map;

/** Reviewed public agency text sources; mirrored by runtime knowledge_sources.py. */
final class KnowledgeSourcePolicy {
    private record Source(String license, String policy, String publisher, List<String> paths) {}
    private static final Map<String, Source> SOURCES = Map.of(
        "www.nimh.nih.gov", new Source("public-domain-nimh", "https://www.nimh.nih.gov/site-info/policies",
            "National Institute of Mental Health (NIMH)", List.of("/health/")),
        "www.nhlbi.nih.gov", new Source("public-domain-nhlbi", "https://www.nhlbi.nih.gov/about/contact/trademark-branding-and-logo",
            "National Heart, Lung, and Blood Institute (NHLBI)", List.of("/health/")),
        "www.nccih.nih.gov", new Source("public-domain-nccih", "https://www.nccih.nih.gov/tools/privacy",
            "National Center for Complementary and Integrative Health (NCCIH)", List.of("/health/")),
        "www.cdc.gov", new Source("public-domain-cdc", "https://www.cdc.gov/other/agencymaterials.html",
            "Centers for Disease Control and Prevention (CDC)",
            List.of("/mental-health/", "/emotional-well-being/", "/social-connectedness/", "/sleep/"))
    );

    static boolean accepts(String url, Map<String,String> metadata) {
        if (metadata == null) return false;
        try {
            URI parsed = URI.create(url);
            Source source = SOURCES.get(parsed.getHost());
            if (!"https".equals(parsed.getScheme()) || source == null || parsed.getUserInfo() != null
                || (parsed.getPort() != -1 && parsed.getPort() != 443) || parsed.getRawQuery() != null
                || parsed.getRawFragment() != null || parsed.getRawPath().contains("%")
                || !parsed.normalize().getPath().equals(parsed.getPath())
                || source.paths().stream().noneMatch(parsed.getPath()::startsWith)
                || !source.license().equals(metadata.get("license"))) return false;
            if (source.license().equals("public-domain-nimh") && !metadata.containsKey("licenseSource")) return true;
            return source.policy().equals(metadata.get("licenseSource")) && source.publisher().equals(metadata.get("publisher"));
        } catch (IllegalArgumentException | NullPointerException e) {
            return false;
        }
    }

    private KnowledgeSourcePolicy() {}
}
