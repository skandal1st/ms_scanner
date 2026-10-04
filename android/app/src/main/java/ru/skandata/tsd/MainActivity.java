package ru.skandata.tsd;

import android.app.Activity;
import android.annotation.SuppressLint;
import android.app.AlertDialog;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.graphics.Color;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

/** Online terminal client. Authentication and picking stay in the existing TSD app. */
public class MainActivity extends Activity {
    private static final String START_URL = "https://skandata.ru/tsd";
    private WebView web;
    private LinearLayout errorPanel;

    static boolean isTerminalUrl(Uri uri) {
        return uri != null && "https".equals(uri.getScheme()) && "skandata.ru".equals(uri.getHost())
            && (uri.getPort() == -1 || uri.getPort() == 443)
            && ("/tsd".equals(uri.getPath()) || "/tsd/".equals(uri.getPath()));
    }

    @SuppressLint("SetJavaScriptEnabled") // Trusted HTTPS terminal origin only; React requires JavaScript.
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        getWindow().setStatusBarColor(Color.rgb(23, 35, 61));
        FrameLayout root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        WebSettings settings = web.getSettings();
        // Required by the React client and its persistent device token.
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setMediaPlaybackRequiresUserGesture(false);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (isTerminalUrl(request.getUrl())) return false;
                // Only terminal pages run inside the authenticated WebView.
                return true;
            }
            @Override public void onPageFinished(WebView view, String url) {
                if (errorPanel.getVisibility() != View.VISIBLE) web.requestFocus();
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) showConnectionError();
            }
            @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
                if (request.isForMainFrame() && response.getStatusCode() >= 400) showConnectionError();
            }
        });
        errorPanel = new LinearLayout(this);
        errorPanel.setOrientation(LinearLayout.VERTICAL);
        errorPanel.setGravity(Gravity.CENTER);
        errorPanel.setPadding(24, 24, 24, 24);
        errorPanel.setBackgroundColor(Color.WHITE);
        TextView message = new TextView(this);
        message.setText(R.string.connection_error);
        message.setTextColor(Color.rgb(23, 35, 61));
        message.setTextSize(18);
        message.setGravity(Gravity.CENTER);
        errorPanel.addView(message);
        Button retry = new Button(this);
        retry.setText(R.string.retry);
        retry.setOnClickListener(v -> {
            errorPanel.setVisibility(View.GONE);
            web.setVisibility(View.VISIBLE);
            web.reload();
        });
        errorPanel.addView(retry);
        root.addView(errorPanel, new FrameLayout.LayoutParams(-1, -1));
        errorPanel.setVisibility(View.GONE);
        setContentView(root);
        if (state == null || web.restoreState(state) == null) loadIntent(getIntent());
    }

    private void showConnectionError() {
        web.setVisibility(View.INVISIBLE);
        errorPanel.setVisibility(View.VISIBLE);
    }

    private void loadIntent(Intent intent) {
        Uri uri = intent.getData();
        web.loadUrl(TerminalLinks.resolve(uri == null ? null : uri.toString()));
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        errorPanel.setVisibility(View.GONE);
        web.setVisibility(View.VISIBLE);
        loadIntent(intent);
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        web.saveState(state);
        super.onSaveInstanceState(state);
    }

    @Override protected void onResume() { super.onResume(); web.onResume(); }
    @Override protected void onPause() { web.onPause(); super.onPause(); }

    @Override public void onBackPressed() {
        // Navigating WebView history can discard the current picking screen.
        new AlertDialog.Builder(this).setTitle(R.string.close_title)
            .setMessage(R.string.close_message)
            .setNegativeButton(R.string.continue_picking, null)
            .setPositiveButton(R.string.close, (dialog, which) -> finish()).show();
    }

    @Override protected void onDestroy() {
        web.destroy();
        super.onDestroy();
    }
}
