package com.signaldvr.app.api

import android.content.Context
import androidx.preference.PreferenceManager
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {

    const val DEFAULT_SERVER_URL = "http://192.168.2.116:8088"
    const val SERVER_URL_KEY = "server_url"

    private var retrofit: Retrofit? = null
    private var currentBaseUrl: String = ""

    fun getApi(context: Context): SignalDvrApi {
        val baseUrl = getBaseUrl(context).trimEnd('/') + "/"

        if (retrofit == null || baseUrl != currentBaseUrl) {
            currentBaseUrl = baseUrl
            retrofit = buildRetrofit(baseUrl)
        }

        return retrofit!!.create(SignalDvrApi::class.java)
    }

    fun getBaseUrl(context: Context): String {
        val prefs = PreferenceManager.getDefaultSharedPreferences(context)
        val saved = prefs.getString(SERVER_URL_KEY, DEFAULT_SERVER_URL)
            ?: DEFAULT_SERVER_URL
        return normalizeServerUrl(saved)
    }

    fun saveBaseUrl(context: Context, value: String): String {
        val normalized = normalizeServerUrl(value)
        PreferenceManager.getDefaultSharedPreferences(context)
            .edit()
            .putString(SERVER_URL_KEY, normalized)
            .apply()
        reset()
        return normalized
    }

    /**
     * Accepted examples:
     *   192.168.2.116
     *   192.168.2.116:9000
     *   signaldvr.local:8088
     *   http://192.168.2.116:8088
     *   https://dvr.example.com
     *
     * A missing scheme becomes http://. A port is never forced.
     */
    fun normalizeServerUrl(value: String): String {
        var normalized = value.trim()
        if (normalized.isBlank()) {
            normalized = DEFAULT_SERVER_URL
        }

        if (!normalized.startsWith("http://", ignoreCase = true) &&
            !normalized.startsWith("https://", ignoreCase = true)
        ) {
            normalized = "http://$normalized"
        }

        return normalized.trimEnd('/')
    }

    fun newConnectionTestClient(): OkHttpClient {
        return OkHttpClient.Builder()
            .connectTimeout(6, TimeUnit.SECONDS)
            .readTimeout(8, TimeUnit.SECONDS)
            .callTimeout(10, TimeUnit.SECONDS)
            .build()
    }

    private fun buildRetrofit(baseUrl: String): Retrofit {
        val logging = HttpLoggingInterceptor().apply {
            level = HttpLoggingInterceptor.Level.BASIC
        }

        val client = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .addInterceptor(logging)
            .build()

        return Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }

    fun reset() {
        retrofit = null
        currentBaseUrl = ""
    }
}
