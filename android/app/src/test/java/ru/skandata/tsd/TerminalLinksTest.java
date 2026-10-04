package ru.skandata.tsd;
import org.junit.Test;
import static org.junit.Assert.assertEquals;
public class TerminalLinksTest {
    private static final String ID = "11111111-1111-4111-8111-111111111111";
    @Test public void shipmentLinkOpensRequestedDocument() {
        assertEquals(TerminalLinks.START_URL + "?document=" + ID, TerminalLinks.resolve("skandata:" + ID));
    }
    @Test public void oldWebLinksAndPairingRemainSupported() {
        String link = TerminalLinks.START_URL + "?pair=SKANDATA%3ATSD%3Aexample";
        assertEquals(link, TerminalLinks.resolve(link));
        assertEquals(TerminalLinks.START_URL + "?document=" + ID, TerminalLinks.resolve(TerminalLinks.START_URL + "?document=" + ID));
    }
    @Test public void malformedAndForeignLinksCannotNavigateAuthenticatedWebview() {
        for (String value : new String[]{null, "skandata:javascript:alert(1)", "skandata:" + ID + "#bad", "https://evil.test/tsd", "https://skandata.ru.evil.test/tsd", "http://skandata.ru/tsd", "https://skandata.ru:123/tsd", "https://skandata.ru/settings"})
            assertEquals(TerminalLinks.START_URL, TerminalLinks.resolve(value));
    }
}
