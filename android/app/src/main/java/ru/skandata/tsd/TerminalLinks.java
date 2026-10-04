package ru.skandata.tsd;
import java.net.URI;
import java.net.URISyntaxException;
final class TerminalLinks {
    static final String START_URL = "https://skandata.ru/tsd";
    static String resolve(String raw) {
        if (raw == null) return START_URL;
        try {
            URI uri = new URI(raw);
            if ("skandata".equals(uri.getScheme())) {
                String id = uri.getSchemeSpecificPart();
                if (uri.getFragment() == null && id.matches("(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))
                    return START_URL + "?document=" + id;
            }
            if ("https".equals(uri.getScheme()) && "skandata.ru".equals(uri.getHost())
                && uri.getUserInfo() == null && (uri.getPort() == -1 || uri.getPort() == 443)
                && ("/tsd".equals(uri.getPath()) || "/tsd/".equals(uri.getPath()))) return raw;
        } catch (URISyntaxException ignored) { }
        return START_URL;
    }
}
