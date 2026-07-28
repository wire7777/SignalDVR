package com.signaldvr.app.ui.settings

import android.content.pm.PackageManager
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.bumptech.glide.Glide
import com.signaldvr.app.R
import com.signaldvr.app.api.ApiClient
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.Request

class SettingsFragment : Fragment() {

    private lateinit var serverAddress: EditText
    private lateinit var testButton: Button
    private lateinit var saveButton: Button
    private lateinit var clearCacheButton: Button
    private lateinit var connectionStatus: TextView

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View {
        return inflater.inflate(
            R.layout.fragment_settings,
            container,
            false
        )
    }

    override fun onViewCreated(
        view: View,
        savedInstanceState: Bundle?
    ) {
        super.onViewCreated(view, savedInstanceState)

        serverAddress =
            view.findViewById(R.id.settings_server_address)

        testButton =
            view.findViewById(R.id.settings_test_connection)

        saveButton =
            view.findViewById(R.id.settings_save)

        clearCacheButton =
            view.findViewById(R.id.settings_clear_cache)

        connectionStatus =
            view.findViewById(R.id.settings_connection_status)

        view.findViewById<TextView>(R.id.settings_app_version).text =
            getString(
                R.string.settings_version_value,
                getAppVersionName()
            )

        val currentUrl =
            ApiClient.getBaseUrl(requireContext())

        serverAddress.setText(currentUrl)
        serverAddress.setSelection(currentUrl.length)
        serverAddress.imeOptions =
            EditorInfo.IME_ACTION_DONE

        serverAddress.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_DONE) {
                testConnection()
                true
            } else {
                false
            }
        }

        testButton.setOnClickListener {
            testConnection()
        }

        saveButton.setOnClickListener {
            saveServerAddress()
        }

        clearCacheButton.setOnClickListener {
            clearArtworkCache()
        }

        testButton.requestFocus()
    }

    private fun getAppVersionName(): String {
        return try {
            val context = requireContext()

            val packageInfo =
                context.packageManager.getPackageInfo(
                    context.packageName,
                    0
                )

            packageInfo.versionName ?: "Unknown"
        } catch (_: PackageManager.NameNotFoundException) {
            "Unknown"
        }
    }

    private fun saveServerAddress() {
        val normalized =
            ApiClient.saveBaseUrl(
                requireContext(),
                serverAddress.text
                    ?.toString()
                    .orEmpty()
            )

        serverAddress.setText(normalized)
        serverAddress.setSelection(normalized.length)

        showStatus(
            getString(
                R.string.settings_saved,
                normalized
            ),
            true
        )

        Toast.makeText(
            requireContext(),
            R.string.settings_saved_toast,
            Toast.LENGTH_SHORT
        ).show()
    }

    private fun testConnection() {
        val normalized =
            ApiClient.normalizeServerUrl(
                serverAddress.text
                    ?.toString()
                    .orEmpty()
            )

        serverAddress.setText(normalized)
        serverAddress.setSelection(normalized.length)

        setBusy(true)

        showStatus(
            getString(R.string.settings_testing),
            null
        )

        viewLifecycleOwner.lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val request = Request.Builder()
                        .url(
                            "${normalized.trimEnd('/')}" +
                                    "/api/live/channels"
                        )
                        .get()
                        .build()

                    ApiClient.newConnectionTestClient()
                        .newCall(request)
                        .execute()
                        .use { response ->
                            if (!response.isSuccessful) {
                                error(
                                    "Server returned HTTP " +
                                            response.code
                                )
                            }
                        }
                }
            }

            if (!isAdded || view == null) {
                return@launch
            }

            setBusy(false)

            result.onSuccess {
                showStatus(
                    getString(
                        R.string.settings_connected,
                        normalized
                    ),
                    true
                )
            }.onFailure { error ->
                val detail =
                    error.message
                        ?.takeIf { it.isNotBlank() }
                        ?: getString(
                            R.string.settings_unknown_error
                        )

                showStatus(
                    getString(
                        R.string.settings_connection_failed,
                        detail
                    ),
                    false
                )
            }
        }
    }

    private fun setBusy(busy: Boolean) {
        testButton.isEnabled = !busy
        saveButton.isEnabled = !busy
        serverAddress.isEnabled = !busy

        testButton.text =
            if (busy) {
                getString(
                    R.string.settings_testing_button
                )
            } else {
                getString(
                    R.string.settings_test_connection
                )
            }
    }

    private fun showStatus(
        message: String,
        success: Boolean?
    ) {
        connectionStatus.text = message

        val colorRes = when (success) {
            true -> R.color.settings_success
            false -> R.color.settings_error
            null -> R.color.settings_secondary_text
        }

        connectionStatus.setTextColor(
            ContextCompat.getColor(
                requireContext(),
                colorRes
            )
        )
    }

    private fun clearArtworkCache() {
        clearCacheButton.isEnabled = false

        Glide.get(requireContext()).clearMemory()

        viewLifecycleOwner.lifecycleScope.launch {
            withContext(Dispatchers.IO) {
                Glide.get(
                    requireContext().applicationContext
                ).clearDiskCache()
            }

            if (!isAdded || view == null) {
                return@launch
            }

            clearCacheButton.isEnabled = true

            Toast.makeText(
                requireContext(),
                R.string.settings_cache_cleared,
                Toast.LENGTH_SHORT
            ).show()
        }
    }
}